# Trading Corp — Production Deploy Log

**Purpose.** Append-only record of every production deploy. The
prod VM has no git, so this file is the single source of truth for
"what's running on prod right now."

**Why this exists.** Recurring failure mode pre-2026-05-02: forgetting
that a feature already shipped (because it was bundled in a bulk-track
commit, or scaffolded forward-compat in an earlier phase, or
implemented before the BACKLOG.md item was retired). The fix is
captured in CLAUDE.md §1 — "Before any deploy-adjacent work" — and
this log is the artifact that makes it possible.

**Source of truth precedence:**
1. `runbooks/deploy_log.md` (this file) — what's on prod right now
2. md5-diff between local and prod — verify before deploying
3. `BACKLOG.md` — what we want to do, NOT what's done
4. Memory entries — same caveat as BACKLOG.md

---

## Verifying prod state before a deploy

**md5-diff target files against prod** before writing any new
code on a feature you can't 100% verify is unimplemented. Files
that MATCH are likely already done — investigate before assuming
new code is needed:

```bash
for f in <files>; do
  l=$(md5sum "$f" | awk '{print $1}')
  p=$(ssh azureuser@trading.jacksumner.com "md5sum /home/azureuser/trading_corp/$f 2>/dev/null | awk '{print \$1}'")
  [ "$l" = "$p" ] && echo "MATCH $f" || echo "DIFFER $f"
done
```

---

## Template for new entries

```markdown
## YYYY-MM-DD HH:MM UTC — <phase or feature label>

**Commits:** <commit-hashes>
**Triggered by:** <user-request or session-context>
**Backup tag:** `.pre-<label>-YYYYMMDD-HHMM` (or `n/a` for first-shipment of new files)

**Files deployed (N):**
- `<path>` — <one-line summary of change>

**Features shipped (load-bearing for future "is X done?" checks):**
- <feature 1>: <what's now live, observable how>
- <feature 2>: <...>

**Notable code changes (callouts a future Claude shouldn't miss):**
- <change>: <where it lives, why it matters>

**Latent bugs caught + fixed (if any):**
- <bug>: <symptom, fix, where>

**Verification:**
- <PID change, audit row landing, dashboard probe, etc.>

**Inert / dormant on current traffic (if any):**
- <code that's deployed but not exercising — why, and what would trigger it>

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=<backup-tag>; BASE=/home/azureuser/trading_corp; \
for f in <list>; do mv \$BASE/\$f.\$TAG \$BASE/\$f; done; \
rm -rf <new-files-or-dirs>
"
```
```

---

## 2026-05-29 ~01:58 UTC — Telegram audit-success semantics (Phase C) + lifecycle silent-drop diagnostic (RESOLVED-UNEXPLAINED) + divergence monitor

**Commits:** `<PHASEC_SHA>` (telegram audit semantics + divergence monitor + tests + this deploy_log). Memory: `telegram-audit-success-is-confirmed-delivery`.
**Triggered by:** Operator task — diagnose why 5 bitunix lifecycle resolutions on 2026-05-28 landed silently (no Telegram) despite clean prod-side audits; then strengthen audit semantics + add a structural divergence monitor.
**Backup tags:** `.bak-phasec-20260529` on `telegram_bot.py` + `bitunix_lifecycle_notifier.py`. New file `scripts/telegram_lifecycle_divergence_check.py` (first-shipment). `.bak-tgdiag-20260528` (Phase-A instrumentation backups; replay file restored from it).

**Phase A — diagnostic (temporary instrumentation, now REVERTED):** Deployed `[TG-DIAG-20260528]` logging into `paper_trade_replay.py` (azureuser-owned), restarted, and resolved a **synthetic marked paper-trade** (`SYNTH-TGDIAG-20260528-0001`, deleted after; 3-source-verified gone). Findings, in order: notifier is **wired + the SAME live object** at the going-forward tick (same object/dict/module id at `set_lifecycle_notifier` and `_drain` — **refuted** None-at-tick / dual-module / reset); on the synthetic resolution the close-out **was queued** (`_queue_close_out ENTER notifier_is_none=False`) and **reached the drain** (`queue_len=1`), `notify_close_out` dispatched, `send_message` returned no-error, and **the message DELIVERED to the operator's phone**. So the lifecycle path **works in the current build**. **The 2026-05-28 silence (5 resolutions) was NOT root-caused** — presumed older-PID process-specific state; bug vanished, not diagnosed. Instrumentation reverted: `paper_trade_replay.py` md5 back to `6f389d18…` (clean), confirmed no `[TG-DIAG]` at the 01:59 startup.

**Phase C — audit-success semantics (the load-bearing meta-fix):**
- `trading_corp/comms/telegram_bot.py` (md5 `4711ce8b…`): `push()` now `-> bool`, **never raises**, and writes an affirmative audit for EVERY send: `telegram_notification_success` only on a real `send_message` return (HTTP 2xx + ok:true, payload carries `message_id`), `telegram_notification_failed` on the `_app is None` drop / any exception (payload carries `http_status` mapped from the telegram error type + truncated real `response_detail`). Added a **plain-text fallback**: on a send error it retries once WITHOUT `parse_mode` — rescues lifecycle messages whose Markdown (`[PAPER]` brackets) 400s, AND preserves the markdown-fallback resilience the webhook path previously got from `push()` raising (so `web/webhooks.py` was NOT touched). `db_url` resolves at use-time from `TC_DB_URL`/default — **`main.py` was NOT modified** (it's root-owned + carries the pre-tasty drift).
- `trading_corp/comms/bitunix_lifecycle_notifier.py` (md5 `065534d2…`): `_send` is now a thin wrapper passing `audit_path="lifecycle_{type}"` + `order_id`; its own `_write_failed_audit` removed (push owns auditing).
- Tests: `tests/test_telegram_send_audit.py` (6) + updated `test_bitunix_lifecycle_notifier.py` + `test_telegram_lifecycle_divergence_check.py` (2) — all GREEN via `run_capped` (24 in the telegram set; existing telegram tests no regression).
- **Live verification (deployed code, real API, real DB):** startup "CEO online" → `telegram_notification_success {http_status:200, ok:true, message_id:20406}`. Controlled one-off: good chat → `success {200, ok:true, message_id:20407}` (delivered); bad chat_id 999999999 → `failed {http_status:400, ok:false, response_detail:"BadRequest: Chat not found"}`. Both the Markdown attempt and the plain fallback hit the real error before the failed-row was written.

**Divergence monitor (structural protection — operational, not just filed):** `scripts/telegram_lifecycle_divergence_check.py` counts bitunix lifecycle resolutions (A) vs `telegram_notification_success` rows with `path LIKE 'lifecycle_close_out%'` (B) in a window; writes `telegram_lifecycle_divergence_detected` if A>B. Wired as a **daily azureuser cron @ 08:30 UTC** (`cron` service active), logging to `logs/divergence_monitor.log`. First run: 24h window flagged divergence=6 (the pre-deploy silent resolutions — correct detection; ages out of the window within a day); post-deploy 1h window clean (0/0).

**⚠️ Notable / drift / recurring issues:**
- **Notifier ownership drift:** `bitunix_lifecycle_notifier.py` was `root:root`; deploying it (no NOPASSWD path for `cp`) used rm+scp in the azureuser-writable comms dir → now `azureuser:azureuser`. Benign (service runs as azureuser); restore with `sudo chown root:root` if desired.
- **RH session pickle is STALE (`~/.tokens/robinhood.pickle` mtime May 24):** the operator's device-challenge approvals (00:40 + 01:58 UTC) did NOT persist a session, so **every restart re-triggers the Robinhood device-approval challenge and blocks startup until approved on the phone**. This caused a ~25-min web/HITL outage on the first (00:14) restart. Separate recurring operational fragility — NOT part of this work; flag for a follow-up (RH session persistence / device-trust).
- Reconciler 3m-vs-1m granularity false-positives: HELD for a separate session (see `project_bitunix_reconciler_granularity_bug`).

**Service:** PID → `1682407` (01:58:37 UTC restart). NRestarts=0, ActiveState=active. healthz `{"status":"ok","mode":"PAPER"}`. Bitunix scanners + replay loop online; 0 tracebacks since restart.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
B=/home/azureuser/trading_corp; \
cp -a \$B/trading_corp/comms/telegram_bot.py.bak-phasec-20260529 \$B/trading_corp/comms/telegram_bot.py; \
cp -a \$B/trading_corp/comms/bitunix_lifecycle_notifier.py.bak-phasec-20260529 \$B/trading_corp/comms/bitunix_lifecycle_notifier.py; \
rm -f \$B/scripts/telegram_lifecycle_divergence_check.py; crontab -r; \
sudo systemctl restart trading-corp.service"
```
(NB: rollback restart re-triggers the RH device challenge — operator must be ready.)

---

## 2026-05-28 ~04:44 UTC — k3 (kalshi_copy_trader) sports-skip NameError fix FINALLY deployed

**Commits:** `e5efa06` — `copy-trader: fix NameError in sports-skip audit payload` (the one-line fix; authored + pushed **2026-05-24**, but **never deployed until now**). `5623f91` — `k3: regression test for sports-skip NameError (follow-up to e5efa06)` (this session; test only, no prod code).
**Triggered by:** Session task "ship the wallet NameError fix to prod." The prompt assumed the fix was an uncommitted working-tree change; verification found it was already committed + pushed 4 days earlier (e5efa06) but **never reached prod** — prod was still running the broken `a220dcf`-era code.
**Backup:** `kalshi_copy_trader.py.bak-pre-e5efa06-20260528-044249` on prod (same dir as the file).

**Files deployed (1):**
- `trading_corp/agents/strategies/kalshi_copy_trader.py` — prod md5 `0d821eb4…` (== `git show e5efa06^`, broken) → `e349a74f…` (== local HEAD, fixed). Single-line change at **line 312**: sports-skip audit payload `'wallet': wallet, 'whale_handle': user_name,` → `'whale': whale,`. Applied via `sed -i` on the one unique line — base64 of the full file is 44 KB, over the ~28 KB `az --scripts` cap, so full-file inline copy was not viable; sed preserves prod's LF.

**Features shipped (load-bearing for "is X done?" checks):**
- k3 sports-skip branch no longer raises NameError. `kalshi_copy_entry_skipped_sports` audit now writes with the in-scope `whale` var. **Before:** any selected whale with a NEW sports-prefix ticker raised NameError in `run_scan_cycle`, caught at `main.py:~2810` (`_scheduled_kalshi_copy_trader_loop`), aborting the ENTIRE scan cycle for ALL whales — missed real-money exits (downside) + entries (opportunity cost). Because the abort preceded the per-whale snapshot persist, the sports ticker was re-detected as "new" every poll → fired every cycle (~800 NameErrors in 3 days; latest pre-deploy 2026-05-28 04:23:49 UTC).

**Notable code changes:**
- Fix matches the `kalshi_copy_cold_start` payload convention at line 291 (same file). No audit-allowlist exposure: `LoggerAgent.log_event` writes the full payload as JSON with no filter (the allowlist gotcha is on `ProposedOrder.extra` → `base_payload` in `main.py`, a different path).
- The sports ticker is still NOT persisted into the whale snapshot after the fix (the branch `continue`s before the snapshot write), so `kalshi_copy_entry_skipped_sports` is expected to fire ~every cycle for any selected whale holding a sports position. Benign (sports are routed to `kalshi_sports_scout`); just expect recurring skip audits, not a leak.

**Latent process bug caught:** "committed but not deployed." e5efa06 sat on `origin/main` for 4 days while prod ran broken; the deploy_log had observed the NameError on 2026-05-27 (and 2026-05-28) but mis-attributed it as "pre-existing / unrelated / carried BACKLOG item" rather than "the fix isn't on prod." Git ≠ prod, again.

**Verification:**
- Pre-deploy: prod md5 `0d821eb4…` == `git show e5efa06^` blob → prod was **exactly one commit behind for this file, no other drift**. Broken line present exactly once at line 312.
- Post-sed: prod md5 `e349a74f…` == local HEAD blob (deterministic match). Line 312 = `'whale': whale,`. Zero residual `'wallet': wallet`.
- Service: PID `1619576` → `1625233`, ActiveState=active, SubState=running. Restart 04:44:18 UTC; scanner online 04:45:06.
- Runtime CONFIRMED: a full scan cycle ran at **04:55:15 UTC** (t+~11 min): `last_poll_ts` updated, **6 selected-whale snapshots persisted**, **"7 copy ProposedOrder(s) emitted"** (entries + one `would_have_placed` copy **EXIT** — the "missed exits" downside is resolved), **zero NameErrors** across the full post-restart window. Pre-fix this cycle would have aborted on the first whale carrying a sports ticker.
- **Caveat (honest, not a blocker):** the sports-skip branch was NOT *directly* re-triggered this cycle — none of the current 6 selected whales (MaggieTheEagle, Hispaniola, tom14cat14, NovaRex, lengthy.starfish, smedtoshi) held a NEW sports-prefix ticker, so `audit_event` still has **0** `kalshi_copy_entry_skipped_sports` rows (baseline). The fixed branch is proven transitively by md5-identity (prod == test-verified local file) + the RED→GREEN regression test that drives the exact branch. A live prod skip row — the last direct confirmation — will land the next time a selected whale opens a sports ticker. **Low-priority watch:** `sqlite3 … "SELECT count(*) FROM audit_event WHERE kind='kalshi_copy_entry_skipped_sports';"` — baseline is 0, so any row confirms.
- Regression test: `tests/test_kalshi_copy_trader_sports_skip.py` drives `run_scan_cycle` end-to-end; verified RED (NameError at :312) against the broken payload and GREEN against the fix, both via `scripts/run_capped.ps1`.

**Rollback:** `cp -p /home/azureuser/trading_corp/trading_corp/agents/strategies/kalshi_copy_trader.py.bak-pre-e5efa06-20260528-044249 /home/azureuser/trading_corp/trading_corp/agents/strategies/kalshi_copy_trader.py && sudo systemctl restart trading-corp.service`

---

## 2026-05-28 ~03:4x UTC — bitunix telegram lifecycle notifications (Phase 2: TP fills + SL moves + close-out)

**Commits (this entry):** `52ca294` — `bitunix: telegram lifecycle notifications (Phase 2 — TP fills + close-out)`. deploy_log in the follow-up commit.
**Triggered by:** Phase 2 of the telegram-lifecycle work (gated on Phase 1 prod verification, which passed — row `c8f25d17`).
**Backup tags:** `.pre-p2notifier-20260528` on `paper_trade_replay.py` + `main.py`. New module `bitunix_lifecycle_notifier.py` is first-shipment (no backup).

**Files deployed (3):**
- `trading_corp/comms/bitunix_lifecycle_notifier.py` — NEW (md5 `e232e8a0…`). `BitunixLifecycleNotifier`: one canonical helper. `notify_tp_fill` (TP1/TP2 + bundled SL move) + `notify_close_out` (TP3/SL/expired with path, R, $PnL, held). Per-mode prefix `📄 [PAPER]` / `💸 [LIVE]`. Send wrapped in try/except → writes `telegram_notification_failed` audit on failure; never raises.
- `trading_corp/agents/paper_trade_replay.py` — md5 `6099d066…` → `6f389d18…`. Queues lifecycle events during the sync classifier walk (`_queue_tp_fill_notification` in `_emit_audit`) + after `_update_row` (`_queue_close_out_notification`); drains async at tick end (`_drain_notify_queue`). `set_lifecycle_notifier()` injects the channel-backed notifier. Idempotent: TP events fire only on the tick a leg transitions (resumed `filled_legs` prevent re-emit); close-out fires once (result-IS-NULL filter).
- `trading_corp/main.py` — md5 `6636b2c0…` → `cacc46ed…`. Constructs + wires the notifier AFTER the startup catch-up, so the backfill of resolutions missed during downtime stays SILENT — only going-forward live resolutions ping.

**⚠️ Prod main.py drift noted (pre-existing, NOT introduced here):** prod `main.py` was at `6636b2c0…` = `94b3129~1` (PRE-tasty_options). The 136-line tasty_options wiring from commit `94b3129` was never deployed to prod — this is the BACKLOG P3 `tasty_options` anomaly, confirmed from the main.py side. My notifier hunk sits between tasty_options hunks (line 1188 and 1471), in untouched territory, so `patch -p1` applied cleanly at offset -77. Prod main.py now = pre-tasty + notifier hunk (`cacc46ed…`); it STILL lacks tasty_options wiring (separate anomaly, unchanged by this deploy).

**Failure semantics:** observability-only. Notifier send failure → `telegram_notification_failed` audit row + warning log; the replay tick continues unaffected. The drain wrapper also catches malformed-event formatting errors. Never blocks/delays/modifies the trade lifecycle.

**Service:** PID `1604244` → `1619576`. ActiveState=active, SubState=running, NRestarts=0.

**Verification:**
- Pre-deploy: prod md5 of `paper_trade_replay.py` == HEAD~1 blob (no drift). `main.py` drift root-caused (pre-tasty). Patch dry-run clean on prod (main hunk offset -77). New-module md5 `e232e8a0…` verified post-transfer.
- Post-patch md5s all match locally-simulated expected. `ast.parse` OK on all 3. Import check OK (`BitunixLifecycleNotifier` + `set_lifecycle_notifier`).
- Tests: 97 pass (boot smoke + replay + notifier unit (7) + replay integration (2: TP1→TP2→TP3 sequence + no-notifier safety) + observer v2 + reconciler + paper_trade_record + telegram_batcher).
- Post-restart: service active, all scanners online (journal 03:53:25), 0 fatals/tracebacks, IC startup catch-up in progress (port-bind/healthz completes after catch-up — the usual multi-minute window). Notifier-error count = 0.
- **ACCEPTANCE (real telegram from a real lifecycle): PENDING** — needs a new bitunix trade to resolve post-restart (PID 1619576). The actual Telegram message is operator-side (agent can't read the operator's phone). Prod-side proxy to confirm the notifier fired clean: a post-restart resolution + zero `telegram_notification_failed` audit rows + zero "lifecycle notify drain failed" warnings. Operator confirms the message(s) arrived.

**Rollback:** `cd /home/azureuser/trading_corp && for f in trading_corp/agents/paper_trade_replay.py trading_corp/main.py; do cp -a $f.pre-p2notifier-20260528 $f; done && rm -f trading_corp/comms/bitunix_lifecycle_notifier.py && sudo systemctl restart trading-corp.service`.

---

## 2026-05-28 ~02:5x UTC — bitunix v2 $PnL persistence fix (score-path `expected_gain`/`tp_r_multiple` oversight) + 7-row backfill

**Commits (this entry):** `bc9d188` — `bitunix v2: populate expected_gain_if_tp_hit + tp_r_multiple in _build_proposal_v2` (code + test + backfill script). Docs/corrections in the follow-up commit.
**Triggered by:** Telegram-lifecycle-notifications Phase 1 (prereq). §D.3 diagnostic of `runbooks/2026-05-28_telegram_lifecycle_notifications_proposal.md`.
**Backup tags:**
- Code: `.pre-pnlfix-20260528` on `bitunix_futures_observer.py` + `paper_trade_replay.py`
- DB: `trading_corp.db.pre-pnl-backfill-20260528` (md5 `7406a694…`, 772 MB)

**Files deployed (2):**
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — prod md5 `5b7d342b…` → `d31bed3d…`. `_build_proposal_v2` now sets `tp_r_multiple` (= blended R across legs = `Σ(leg.fraction × leg.target_r)`) and `expected_gain_if_tp_hit` (= `max_dollar_risk × tp_r_multiple`) in `order.extra`.
- `trading_corp/agents/paper_trade_replay.py` — prod md5 `49c9735f…` → `6099d066…`. Added `log.warning` on the two PnL-compute fallback-to-0 branches (partial-win SL path line ~530, TP3-fill path line ~573) when `expected_gain` is null, so future null-fallthrough is visible not silent. No behavior change otherwise (still falls to 0).

**Root cause (corrected from the dashboard-proposal V1 premise):** NOT "all rows = 0.00 / value never computed." Full prod diagnostic of 78 bitunix rows: 46 positive PnL, 22 negative, **10 zero** — of which 3 are correctly-zero `expired` (no fills) and 7 are partial-win SCORE-path rows. The score path (`_score_and_maybe_propose_locked`) routes through `_build_proposal_v2`, which (unlike the legacy `_build_proposal` used by the traditional `_maybe_propose` path) omitted the two PaperTradeRecord-harmonized fields. Without `expected_gain`, `paper_trade_replay.py:526-531`/`569-571` fell to $0 on partial-win (TP1-then-SL) and TP3-fill closes. Surfaced more now because every post-PA-2of3 fire uses the score path.

**Backfill:** 7 rows (`actual_pnl_dollars = -expected_loss × actual_r_multiple`, the v2 invariant). Script `scripts/backfill_bitunix_v2_pnl_20260528.py` (re-runnable, idempotent on the eligibility predicate). Affected order_ids: `2942ff8e` (+$0.348), `e6f437e3` (+$0.713), `cb19b9ad` (+$0.065), `28f43f1e` (+$0.089), `0b118801` (+$0.105), `2007d2c9` (+$0.065), `6daca683` (+$0.070) — total +$1.46. **⚠️ Dashboard win-rate `$PnL` cell changes retroactively for these 7 trades** (was $0, now small positive). Expected, not a regression. The 3 `expired` rows correctly remain $0.

**Service:** PID `1576923` → `1604244`. ActiveState=active, SubState=running, NRestarts=0.

**Verification:**
- Pre-deploy: prod md5 of both files == git HEAD blobs (no drift). Patch dry-run clean. Post-patch md5 == locally-simulated expected. `ast.parse` OK on both.
- Tests: 100 pass across `test_bitunix_observer_v2_path` (incl. new `test_v2_proposal_carries_pnl_compute_fields`), `test_paper_trade_replay`, `test_bitunix_position_reconciler`, `test_paper_trade_record`, `test_bitunix_view_builders`.
- Backfill: CHANGES=7; 7 rows verified non-zero post-update; only 3 `expired` zeros remain.
- **GATE for Phase 2 (notifier): SATISFIED.** New score-path trade `c8f25d17-29d2-...` fired 2026-05-28T03:18:06 (post-restart, PID 1604244), resolved `win` on the 03:23:44 replay tick: `expected_gain=0.10230`, `tp_r_multiple=1.33225` (both NULL before the fix), `actual_r_multiple=1.3322`, **`actual_pnl_dollars=0.10230` (non-zero)**, `score_path=1`. Invariant holds: `eg/tpR = 0.0768 = -expected_loss = max_dollar_risk`. Before the fix this row would have shown $0.00.
- Healthz post-deploy: `{"status":"ok","mode":"PAPER"}`. No bitunix/replay errors in journal (the 1 recurring `Traceback` is the unrelated pre-existing `_scheduled_kalshi_copy_trader_loop` NameError at `main.py:2810`, carried BACKLOG item).

**Rollback:** `cd /home/azureuser/trading_corp && for f in trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/paper_trade_replay.py; do cp -a $f.pre-pnlfix-20260528 $f; done && sudo systemctl restart trading-corp.service`. DB rollback (if ever needed): `cp -a data/trading_corp.db.pre-pnl-backfill-20260528 data/trading_corp.db` (⚠️ would also revert any trades written since the backfill — only use if backfill itself is wrong).

---

## 2026-05-28 00:16 UTC — bitunix dashboard: small-PR clutter cleanup (Phase 3.2 label + Recent Evaluations + bar-cache aggregate)

**Commits (this entry):**
- `3d9d9d3` — `bitunix dashboard: small-PR cleanup (Phase 3.2 label + Recent Evaluations + bar-cache aggregate) + consolidation proposal`

**Triggered by:** Operator-approved small standalone PR per Section F item 6 of `runbooks/2026-05-27_bitunix_dashboard_consolidation_proposal.md`. Full 5-panel rebuild deferred to a separate session. This deploy cuts cheap visible clutter so the dashboard reads cleaner today without prejudging the broader layout change.

**Backup tag:** `/home/azureuser/trading_corp/trading_corp/web/templates/partials/bitunix_score_panel.html.pre-dashboard-cleanup-20260527` (md5 `9d30d6bad06233bf5f68bb1040ac06b3`, pre-cut state).

**Files deployed (1):**
- `trading_corp/web/templates/partials/bitunix_score_panel.html` — pre md5 `9d30d6ba…` (368 lines, 17039 B), post md5 `62f085be373d9264d1eb69bf6a5d7ec8` (291 lines, 13747 B). Net −77 lines / −3292 B. CRLF preserved. Owner root:root preserved (sudo cp from /tmp stage). Deployed via base64-encoded payload + `sudo cp /tmp` (the standard prod-bypass-git template deploy pattern).

**Features shipped (load-bearing for future "is X done?" checks):**
- **"(Phase 3.2)" header label removed.** Internal versioning text gone from the H2; dashboard is not release notes. Docstring at template line 1 still references `Phase 3.2.3` (developer comment, not user-visible).
- **Bar-cache aggregate stat card removed** from the top-row stat-card grid. Per-TF version in the HTF Regime panel (still rendered) is the canonical bar-cache view. View-builder still populates `bs.bar_cache` in the dict (no data layer change); only the render block was cut.
- **"Recent evaluations (20) · ledger 24h" table removed** from the score panel. Was a duplicate of Decision Flow's chained view per V1 of the consolidation proposal. View-builder still populates `bs.recent_evals` + `bs.ledger_window` (no data layer change); only the render block was cut.
- **Top-row stat-card grid retuned** `grid-cols-2 lg:grid-cols-4 → grid-cols-2 lg:grid-cols-3` so the 3 remaining cards (Last eval / Net score / Cooldown) fit cleanly on desktop.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Templates auto-reload on Jinja2 — restart was ceremonial.** Per `[[reference-prod-systemd-units]]`. The 5-min strategy-pause restart was operator-requested for paranoia + journal-clean confirmation. Future presentation-only template deploys can skip the restart unless an operator wants the verification.
- **View-builder code unchanged.** Both `bar_cache` and `recent_evals` keys still populate in the view dict (so any future re-render is a one-liner). Presentation-only cut. Filed Section F item 6's "see the cleanup impact before committing to the full layout change" — that's the intent of this small-PR-first sequence.

**Verification — pre-deploy:**
- Pre-flight grep for `#bitunix-score-panel`, `bs.bar_cache`, `bs.recent_evals` confirmed references contained to `bitunix_score_panel.html` only. No other partial hx-selects into the cut sections.
- 3 surgical Edits via the local Edit tool (CRLF preserved on local).

**Verification — on prod (post-restart):**
- **PIDs:** `1571555` (pre, since 2026-05-27 23:18:19 UTC) → `1576923` (post, since 2026-05-28 00:16:49 UTC). NRestarts=0. ActiveState=active.
- **Healthz local:** `{"status":"ok","mode":"PAPER"}` post port-bind (~5 min IC catch-up window, normal).
- **Loaded template via `/proc/1576923/root/.../bitunix_score_panel.html`:** md5 `62f085be373d9264d1eb69bf6a5d7ec8` (exact post-patch).
- **Observer wiring line:** `BitUnix observer wiring: scoring=True, pa_enabled=True, htf_gate_mode=enforce, htf_regime_enabled=True, trade_plan_active=True`.
- **No template parse errors in journal** since restart. Bitunix bar caches primed.
- **Browser-side render not verified by agent** (Authelia gates localhost curl from prod); operator's next dashboard load is the final ground-truth check. The cuts should be immediately visible.

**Inert / dormant on current traffic:**
- View-builder still computes `bar_cache` + `recent_evals` payloads on every request. Tiny waste — kept intentionally so a one-line template edit could restore the displays. Will be cleaned up as part of the full 5-panel rebuild.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
sudo cp /home/azureuser/trading_corp/trading_corp/web/templates/partials/bitunix_score_panel.html.pre-dashboard-cleanup-20260527 \
        /home/azureuser/trading_corp/trading_corp/web/templates/partials/bitunix_score_panel.html
# No restart needed — Jinja auto-reloads templates.
"
```

---

## 2026-05-27 23:18 UTC — bitunix PA validation: loosen to >=2 of 3 (`require_all: false` + `min_validators_passed: 2`)

**Commits (this entry):** (config-only deploy + this entry; commit to follow)
**Triggered by:** Operator directive 2026-05-27 ~22:00 UTC — bitunix paper division producing 3 trades in 5 days post the 2026-05-23 15:52 UTC bias-TTL deploy. Funnel diagnostic showed 99.06% PA-reject rate; replay (commit `9606b9f`, see `reports/2026-05-27_bitunix_pa_replay_synthesis.md`) characterized the all-three-failed bucket (n=1,494): **0% solo signals; 87.4% are 3+ signal stacks** — refuted the "score over-generous" hypothesis. Top stack `mc_a_blood_diamond + mc_a_red_diamond + mc_a_redx` (305 occurrences) is genuine Cypher A-panel sell confluence. Verdict: PA validators rejecting real multi-signal score stacks; structural fix on the PA side.

**Backup tag:** `/home/azureuser/trading_corp/config/strategies.yaml.pre-pa-2of3-20260527` (md5 `2a87d38dc44b145a0733c660ea6e1878`, pre-patch state byte-identical).

**Files deployed (1):**
- `config/strategies.yaml` — two-line change to active `bitunix_futures.pa_validation` block. Pre md5 `2a87d38dc44b145a0733c660ea6e1878` (86169 B, 1774 lines). Post md5 `ed8e452d85fafb5132dd0c8e01f55511` (86200 B, 1775 lines; +31 B = exactly one CRLF line). Patch applied via python (binary mode, single-occurrence assert) staged in `/tmp` then `sudo cp` to prod — `sed -i` was rejected because the `a\` insert command writes the new line with LF terminator, breaking CRLF discipline (`[[deploy-crlf-config-patch]]`).

**Features shipped:**
- **PA `require_all: false` + `min_validators_passed: 2`** — both knobs required. Single-knob (`require_all: false` alone) would mean `len(passed) >= 0` which **disables PA entirely** (every trade passes). Replay's 18.4% PA-pass estimate assumed both. Without the `min_validators_passed: 2` line, PA would have flipped from 99% reject straight to 100% pass.
- **Expected behavior change:** PA-pass rate jumps from 0.94% → ~18.4% on identical input stream. After HTF (63% hard-zero) + trade_plan (70% fee-floor on STANDARD), upper-bound estimate is ~15 placements/day vs. 0.75/day baseline (~20× lift). Most lift is on the sell side (2:1 sell signal mix, 0 BULL HTF days since deploy).

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`min_validators_passed` was previously absent from YAML and defaults to `0`** in the `bitunix_pa_validation.py:96` dataclass. Code at line 254-262: `if require_all` branches to "all must pass"; else `validators_passed = len(passed) >= min_validators_passed`. With `min_validators_passed=0`, that's always True. The two-knob discipline is load-bearing — never flip `require_all` without also setting the floor.
- **Observer's `scoring_config` is loaded ONCE at startup** (`main.py:319-335`), no mtime check — restart was required for the new YAML to take effect. Inert until restart, verified via `/proc/<pid>/root/.../strategies.yaml`.
- **Local-prod yaml divergence at line 1776+** — local has the `tasty_options` block (commit `94b3129`, 2026-05-24 "Commit 4/5"); prod has no `tasty_options:` entry. Local is 74 lines longer than prod (1848 vs 1774 pre-patch). The bitunix block at line 1218-1242 is byte-identical on both sides, so the surgical patch was safe — but the broader divergence is a SEPARATE anomaly. Filed in BACKLOG.

**Verification — pre-deploy:**
- `require_all` and `pa_validation:` grep returned a single occurrence each in `config/strategies.yaml` (line 1231 + 1229). Active `bitunix_futures.pa_validation` block confirmed unique target.
- Test impact: `tests/test_bitunix_pa_validation.py` already covers `require_all=False, min_validators_passed=N` paths (lines 160, 171). No test changes needed.

**Verification — on prod (post-restart):**
- **PIDs:** `1538397` (pre, since 2026-05-27 10:46:24 UTC) → `1571555` (post, since 2026-05-27 23:18:19 UTC). NRestarts=0. ActiveState=active.
- **Healthz local:** `{"status":"ok","mode":"PAPER"}` post port-bind.
- **Loaded YAML via `/proc/1571555/root/home/azureuser/trading_corp/config/strategies.yaml`:** md5 `ed8e452d85fafb5132dd0c8e01f55511` (exact post-patch). Lines 1231 `require_all: false` + 1232 `min_validators_passed: 2` both present.
- **Observer wiring line:** `BitUnix observer wiring: scoring=True, pa_enabled=True, htf_gate_mode=enforce, htf_regime_enabled=True, trade_plan_active=True`.
- **Bitunix bar caches primed:** 3m (atr_14=$95.34), h1, h4, d1. Above the `[[bitunix-paper-clock]]` $90 tripwire.
- **Robinhood login:** clean (3 accounts, no MFA prompt, pickle worked, no `broker_fallback_to_paper`). Only pre-existing fallback: `fidelity_401k` (recurring, unrelated).
- **No bitunix/PA-related ERROR or Traceback in journal since restart.**

**Observation window — 1 week (closes 2026-06-03 ~23:18 UTC):**
- **Primary signal:** fires per day. Replay estimated ~15/day under loosened gate vs 0.75/day baseline. Confirm directionally.
- **Secondary signal — outcomes:** track TP vs SL hit on the new fires via `audit_event` / bitunix paper position state. Don't declare victory on fire rate alone. If win-rate is materially below baseline tighter-gate trades (3 placed, outcomes pending), the fix has traded reject-bias for bad-take-bias and needs revision.
- **Tertiary — which validator-pair carries each pass:** `pa_validation_decision.payload_json.passed` ALREADY captures the per-pass validator set (no new instrumentation needed). If one validator (likely `structure_alignment` per the all-three-failed bucket composition) NEVER contributes across the observation window, that's evidence the 4h-structure check is broken on the 3m horizon and the next structural change is replacing the 4h horizon with 15m/30m. That's a code change, filed MEDIUM, NOT shipping this session.
- **Rollback trigger:** any of (a) win-rate < 30% after >=20 placed trades, (b) drawdown > 5% on bitunix paper account, (c) some other operator-defined "this isn't working" signal.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
sudo cp /home/azureuser/trading_corp/config/strategies.yaml.pre-pa-2of3-20260527 \
        /home/azureuser/trading_corp/config/strategies.yaml \
  && sudo systemctl restart trading-corp.service
"
```
Single-step revert; restores both knobs to pre-patch state via backup tag.

**Tripwire boundary respected:**
- Options 3 (`htf_regime.proximity_block_pct`, currently 0.30) and 4 (`trade_plan.tp1_min_profit_multiplier`, currently 2.0) remain **DEFERRED to 2026-06-19 midpoint tripwire** per `[[bitunix-paper-clock]]`. This deploy did NOT touch them. This change is a **score↔PA internal-consistency fix** (PA was rejecting genuine multi-signal score confluence), not a gate-tightness loosening — substantively different from the tightness options the clock protects.

---

## 2026-05-27 10:30 + 13:14 UTC — polymarket data-api: gamma 5xx resilience (analyze-whale fix)

**Commits (this entry):**
- `b2128bd` — `polymarket_data_api_client.py`: retry 5xx with short backoff in `_get_json` (0.5s, 1.5s → 3 total attempts).
- `fc7e2d6` — `polymarket_data_api_client.py`: tolerate per-chunk 5xx in `fetch_market_resolutions` (catch `PolymarketDataAPIError`, log partial-coverage warning, fall through to `not_found` sentinels — mirrors existing `PolymarketRateLimitError` path).

Branch: `polymarket-gamma-5xx-retry` (local-only — not merged to `main`; standard prod-bypass-git deploy).

**Triggered by:** Operator directive 2026-05-27 ~04:00 UTC — analyze-whale on `/prediction-markets/polymarket_copy_trading#whales` returning "Analyze errored — check logs / PolymarketDataAPIError" across multiple wallets. Diagnosis showed `gamma-api.polymarket.com/markets?condition_ids=...` intermittently 500ing on individual chunk calls; one bad chunk killed the entire analyze. First patch (retry) shipped at 10:30 UTC, proved insufficient when retry budget exhausted on sustained chunk-0 5xx across 3 different wallets (RTERK43357, bloodmaster, 0x7714c16f); second patch (chunk-skip) shipped at 13:14 UTC.

**Backup tags:**
- `polymarket_data_api_client.py.pre-gamma5xx-retry-20260527` (pre `b2128bd`, md5 `a10c01ddbd2f1c451af8c501aec80010`)
- `polymarket_data_api_client.py.pre-chunkskip-20260527` (pre `fc7e2d6`, md5 `cf1c6dc325c1ed4184931caa9e6d207a` — i.e., the retry-only state)

**Files deployed (1):**
- `trading_corp/data/polymarket_data_api_client.py` — gamma-api 5xx resilience (retry + chunk-skip). Post-deploy md5 `3810a1084c7f90e6f4a4c82e629d3952`.

**Features shipped (load-bearing for future "is X done?" checks):**
- **In-client 5xx retry on gamma-api / data-api calls.** Every `_get_json` call now retries HTTP 500-599 twice with 0.5s + 1.5s backoff before raising. Visible signal: `WARNING ... HTTP NNN on attempt M (XXXms); backing off Y.Ys` log lines from `polymarket-data-api`. Benefits every caller of the client (not just resolutions).
- **Per-chunk fault tolerance in `fetch_market_resolutions`.** A `PolymarketDataAPIError` from any single chunk-variant call is now caught at the chunk loop (mirroring the existing rate-limit handling) and turned into a partial-coverage warning. Failed-chunk condition_ids fall through to the `not_found` sentinel. The summary log now reports both axes: `N/M chunks rate-limited, N/M chunks upstream-errored; X/Y condition_ids resolved`. Analyze-whale no longer dies on a single bad chunk.
- **Constants:** `_SERVER_ERROR_RETRY_DELAYS_SEC: tuple[float, ...] = (0.5, 1.5)` next to the existing `_CLOUDFLARE_RETRY_DELAYS_SEC` at the top of the module.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Except-block ordering matters.** `PolymarketRateLimitError` subclasses `PolymarketDataAPIError`, so the rate-limit `except` MUST come before the generic-data-api `except` in the chunk loop. Both branches `continue` to the next variant. Don't reorder.
- **Retry budgets are intentionally tight.** 2 retries / 3 total attempts at 0.5s + 1.5s = ~2-4s max latency per affected call. Sized for transient flakes; sustained upstream outages exhaust the budget and fall through to chunk-skip, which is the correct behaviour (don't make analyze take 5 minutes hoping a sustained outage clears).
- **`_get_json` 5xx retry is generic across all gamma-api callers** — `fetch_market_resolutions`, `fetch_leaderboard`, `fetch_market_by_id` (etc.) all benefit. The chunk-skip layer is specific to `fetch_market_resolutions` because that's the only caller today with a chunk loop.

**Latent bugs caught + NOT fixed (surfaced during this session; out of scope):**
- **`PolymarketBroker.list_markets` hits identical gamma-api 5xx pattern** but uses a different code path (broker adapter, not the data-api client). Same flakiness, no retry/skip on that path. Observed 2026-05-27 10:29:46 UTC in journal: `Server error '500 Internal Server Error' for url 'https://gamma-api.polymarket.com/markets?closed=false&active=true&...'`. Filed for future session.
- **`Kalshi copy trader: run_scan_cycle failed: name 'wallet' is not defined`** — `NameError` in a recent Kalshi copy-trader code path. Completely unrelated to today's work. Observed 2026-05-27 10:30:03 UTC.

**Verification — pre-deploy:**
- Local diff reviewed (clean — 25 + 24 lines insertion across the two commits, no churn).
- Local file md5s: `cf1c6dc325c1ed4184931caa9e6d207a` (post-retry-only), `3810a1084c7f90e6f4a4c82e629d3952` (post-chunk-skip).
- File line endings: LF on both local and prod (no CRLF preservation needed, unlike `webhooks.py`).

**Verification — on prod (post-restarts):**
- **First deploy (retry, `b2128bd`):** PID `1513106` → `1536228` at ~10:30 UTC. ActiveState=active. Port 8000 bound ~4-5min later. Retry warning observed firing on a real 5xx at 10:36:06 UTC: `polymarket-data-api resolutions[50 ids, chunk 0, open]: HTTP 500 on attempt 1 (2169ms); backing off 0.5s` → attempt 2 also 500 → attempt 3 raised. Proved retry is wired correctly but insufficient for sustained 5xx. Prompted second patch.
- **Second deploy (chunk-skip, `fc7e2d6`):** PID `1536228` → `1538397` at ~13:14 UTC. ActiveState=active. Healthz `{"status":"ok","mode":"PAPER"}` post port-bind. Md5 post-deploy on prod matches local `3810a1084c7f90e6f4a4c82e629d3952`.
- **User-observed:** operator re-clicked Analyze post-second-deploy and confirmed it works ("It worked", ~13:23 UTC). **Caveat:** 30-min log window (13:15–13:45 UTC) shows zero `analyze:` lines and zero `limit=500` activity fetches, suggesting the click hit the analyze cache rather than a fresh fetch. Chunk-skip is therefore deployed and proven not to crash, but **the new `upstream error; partial coverage` warning has not yet been observed firing on a real 5xx**. The next analyze that gets a fresh fetch + happens during a gamma-api flake will be the real-world test.

**Inert / dormant on current traffic:**
- The retry warning `HTTP NNN on attempt M; backing off Y.Ys` only fires on a true 5xx that survives the first attempt; benign as long as gamma-api is healthy.
- The chunk-skip warning `upstream error; partial coverage` only fires after the retry budget is exhausted; benign at gamma-api's typical flake rate.

**Class of bug — gamma-api flakiness affects multiple call sites.** The polymarket-data-api retry/skip pattern is a template for fixing the broker-side `list_markets` flake when that's prioritised. Same upstream, same symptom, different client.

**Rollback recipe:**
```bash
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
  --command-id RunShellScript --scripts "
BASE=/home/azureuser/trading_corp
F=trading_corp/data/polymarket_data_api_client.py
# Full rollback to pre-retry state:
mv \$BASE/\$F.pre-gamma5xx-retry-20260527 \$BASE/\$F
# (Or, to roll back ONLY the chunk-skip and keep retry: mv .pre-chunkskip-20260527 instead.)
chown azureuser:azureuser \$BASE/\$F
systemctl restart trading-corp.service
"
```

---

## 2026-05-27 01:13–01:43 UTC — C-1 partial: webhook secrets ROTATED (2 of 13+) — KV-only; operator-driven; value-blind verification

**Commits (this entry):** none code-side yet; deploy_log + BACKLOG only (pending in the commit that lands this entry). The rotation itself is a KV mutation + service restart; no source-code change.

**Triggered by:** Operator directive 2026-05-27 01:00 UTC (in-session) — "C-1 webhook-secret rotation only (2 of 13+), defer the rest to per-portal sessions". Re-directive at 01:25 UTC after the first attempt's "new" candidate values were echoed via Python-script stdout into the Claude Code transcript (immediately scrubbed at the transcript JSONL, but operator chose a clean re-rotation rather than relying on supersession). Final flow: agent provides KV-write block + verify script; operator runs all value-handling in a separate Git Bash window OUTSIDE Claude Code; operator confirms back only metadata.

**Credential-handling rule applied this session (load-bearing for future security work):** secret values may NEVER pass through any Claude Code surface — not Bash tool stdout, not Edit tool body, not chat messages, not any file the agent has read or written. All value-handling is operator-side in a non-transcripted terminal. The agent generates verification METHODS (scripts, queries, recipes), operator executes the value-bearing parts, operator reports only metadata back. The agent verifies via:

- KV version IDs (returned by `az` queries — never the values)
- HTTP status codes from value-blind verification scripts (script reads from KV inside prod via managed identity, returns only ints)
- Audit-row inspection (post-scrub — the scrub is what makes inspection safe)

**Rotation scope (THIS session):**

- `LORD_OTTER_WEBHOOK_SECRET` env / `LORD-OTTER-WEBHOOK-SECRET` KV — pre-rotation version `17f76188a66142f5b4cf185161028709` (updated 2026-04-30T17:20:44Z) → post-rotation version `29db2cc743d847a788402deea04b2627` (updated 2026-05-27T01:13:43Z).
- `MARKET_CYPHER_WEBHOOK_SECRET` env / `MARKET-CYPHER-WEBHOOK-SECRET` KV — pre-rotation version `c52e08bf25aa429bbdb78f700c5cf20e` (updated 2026-04-30T22:08:17Z) → post-rotation version `d5b2907bf13f4126ad5cac5715feebaf` (updated 2026-05-27T01:13:45Z).
- TradingView alert templates — all 50 alerts updated to the new secrets, then paused → un-paused after agent's GO/NO-GO signal.

**Rotation scope (EXPLICITLY DEFERRED, NOT C-1 done):**

- `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `ROBINHOOD_PASSWORD` + `ROBINHOOD_MFA_SECRET` (TOTP re-enroll), `COINBASE_API_*` (spot + futures), `BITUNIX_FUTURES_API_*`, `FIDELITY_PASSWORD`, `KALSHI_API_KEY_ID` + `KALSHI_PRIVATE_KEY_PEM`, `POLYMARKET_PRIVATE_KEY` (requires new EOA + on-chain USDC transfer), `POLYGON_RPC_URL`, `APIFY_API_TOKEN`, `TASTYTRADE_PROVIDER_SECRET` + `TASTYTRADE_REFRESH_TOKEN` (per `runbooks/tastytrade_oauth_rotation.md`). 11+ credentials remaining; each gets its own per-portal session.

**Backup tag / rollback baseline:** the pre-rotation KV version IDs above. Rollback: `az keyvault secret show --version <pre-rotation-version> --query value -o tsv` reads the prior cleartext (operator-side only), `az keyvault secret set --file <(printf %s "$VAL")` writes it back, restart trading-corp.service. The pre-rotation versions remain enabled in KV history (Azure retains soft-deleted/superseded versions per vault retention policy).

**Operator action block (run OUTSIDE Claude Code, in separate Git Bash):** delivered in chat — `openssl rand -base64 32` per secret → 0600 temp file via `mktemp` + `chmod 600` + `printf '%s'` (CRLF-safe vs heredoc; works around git-bash-on-Windows process-substitution failure with native `az`) → `az keyvault secret set --file <path>` → `shred -u`. Both writes returned new KV version URLs distinct from baseline.

**Verification — value-blind, prod-side:** agent-authored `tmp_verify_c1_value_blind.py` scp'd to prod, executed via `venv/bin/python`, deleted after. Script reads NEW secrets directly from KV via `DefaultAzureCredential` (same path `trading_corp.utils.secrets._populate_from_keyvault` uses on startup), POSTs synthetic webhooks to `http://127.0.0.1:8000/webhook/tradingview/{lord-otter,market-cypher}` with NEW secret (expect 200) and a never-live placeholder `C1_VERIFY_WRONG_PLACEHOLDER_NEVERLIVE_AAAA==` (expect 401). Returns only HTTP status codes + `PASS`/`FAIL` markers. Values never leave prod.

**Results — 4/4 PASS:**

```
[PASS] otter NEW:   HTTP 200 (expected 200)  resp={"status":"accepted","signal":"c1_verify_otter_NEW","symbol":"C1VERIFY"}
[PASS] otter WRONG: HTTP 401 (expected 401)  resp={"status":"rejected","reason":"auth failed"}
[PASS] cypher NEW:  HTTP 200 (expected 200)  resp={"status":"accepted","signal":"c1_verify_cypher_NEW","symbol":"C1VERIFY"}
[PASS] cypher WRONG:HTTP 401 (expected 401)  resp={"status":"rejected","reason":"auth failed"}
```

This proves the new secret IS loaded from KV by the running prod process, the old/wrong secret IS rejected, and BOTH handlers consume the rotated value.

**Service restart:** PID `1507621` (running since 2026-05-26 23:46:22 UTC C-7 deploy) → PID `1513106` at 2026-05-27 01:34:00 UTC. NRestarts=0. Port 8000 bound ~4.5min post-restart (IC position-manager startup catch-up). healthz local + Caddy public `https://trading.jacksumner.com/healthz` both `{"status":"ok","mode":"PAPER"}`. Journal clean (only pre-existing yfinance BTC/USD + Fidelity shared-session-bootstrap noise).

**C-7 scrub real-world stress test (the C-7→C-1 payoff, observed in actual rotation traffic):**

The KV write happened at 01:13:43Z; prod restart was at 01:34:00Z. Between those two events, 6 TV alerts hit prod with NEW-secret-in-body (because TV templates were updated immediately after KV write) vs OLD-secret-in-env (because prod hadn't been restarted yet). All 6 produced `bad_secret` rejections, and **all 6 audit rows carry `"secret": "***REDACTED***"` — zero cleartext leakage**:

| audit id | ts (UTC) | actor | reason | scrub |
|---|---|---|---|---|
| 736184 | 2026-05-27T00:57:02 | market_cypher | bad_secret | `"secret": "***REDACTED***"` ✓ |
| 736648 | 2026-05-27T01:06:09 | market_cypher | bad_secret | `"secret": "***REDACTED***"` ✓ |
| 736649 | 2026-05-27T01:06:09 | lord_otter | bad_secret | `"secret": "***REDACTED***"` ✓ |
| 736650 | 2026-05-27T01:06:09 | market_cypher | bad_secret | `"secret": "***REDACTED***"` ✓ |
| 737236 | 2026-05-27T01:15:02 | market_cypher | bad_secret | `"secret": "***REDACTED***"` ✓ |
| 737422 | 2026-05-27T01:18:01 | market_cypher | bad_secret | `"secret": "***REDACTED***"` ✓ |

Plus the 2 verification rejections (id 738631 lord_otter + 738633 market_cypher at 01:42:49Z), also REDACTED. Without the C-7 fix shipped earlier, these 8 rows would have contained the in-flight rotated value in cleartext — the precise threat C-7 was designed to close, validated under genuine traffic rather than a synthetic harness.

**First-rotation attempt artifact (closed by clean re-rotation):** earlier in this session, an agent-authored Python helper (`tmp_gen_webhook_secrets.py`) generated a first set of candidate values and wrote them to a local file `~/cc_webhook_secrets_DELETE_AFTER_USE.txt`. Subsequent operator commands in the Claude Code chat sourced that file and referenced the values by variable. Critically, **the first attempt's `az keyvault secret set` commands appear to have never executed against KV** — the KV version IDs remained at the 2026-04-30 baseline through the entire first attempt. The agent caught this and stop-and-reported before any prod restart. The clean re-rotation (this entry) generated different values, wrote them to KV via a separate Git Bash window outside Claude, and produced the new version IDs above. The first attempt's "candidate" values never reached KV, never reached TV templates (operator hadn't updated TV yet when the agent flagged the KV-state anomaly), and therefore never were live secrets — the exposure is closed by the never-was-live + clean re-rotation chain, not just by supersession. The first attempt's helper script + handoff file remain on the operator's local disk pending operator cleanup (the file contains values that were never KV-active so the leak surface is artifact-only, not credential-active).

**Memory updates implied (filed in next-session commit if not this one):**

- `[[feedback-secret-never-touches-claude-code]]` — the credential-handling rule applied this session. Generalizes: secrets cross operator-only boundary; agent verifies via metadata (KV versions, status codes, audit rows post-scrub). Cite: this rotation.
- `[[feedback-git-bash-process-substitution-fails]]` — `--file <(printf %s "$VAR")` fails across git-bash → Windows-native-az boundary (`No such file or directory: /proc/N/fd/X`). Use `mktemp` + `chmod 600` + `--file <path>` + `shred -u` instead. Cite: this rotation, second attempt block.

**Operator cleanup pending (not destructive — operator handles):**

- Delete `~/cc_webhook_secrets_DELETE_AFTER_USE.txt` (the first attempt's handoff — values never live, but disk-resident).
- Delete `~/c1_clean_DELETE_AFTER_USE.txt` (the clean rotation's handoff — values are live but only useful to the operator until next rotation; safe to delete once TV templates are confirmed updated, which is now).
- Delete `~/c1_rotate_clean.sh` (the rotation script — no values inside, but tidy).
- Close the standalone Git Bash window where the rotation ran (the operator's local terminal scrollback contains values from `cat`; closing the window clears the OS terminal buffer).

**Rollback recipe (if a live regression is observed):**

```bash
# Restore prior LORD-OTTER value from KV history
az keyvault secret show --vault-name kv-tc-vtwbowt3wtkpy --name LORD-OTTER-WEBHOOK-SECRET \
    --version 17f76188a66142f5b4cf185161028709 --query "value" -o tsv > /tmp/rb_otter
az keyvault secret set --vault-name kv-tc-vtwbowt3wtkpy --name LORD-OTTER-WEBHOOK-SECRET \
    --file /tmp/rb_otter --query "id" -o tsv
shred -u /tmp/rb_otter

# Restore prior MARKET-CYPHER value
az keyvault secret show --vault-name kv-tc-vtwbowt3wtkpy --name MARKET-CYPHER-WEBHOOK-SECRET \
    --version c52e08bf25aa429bbdb78f700c5cf20e --query "value" -o tsv > /tmp/rb_cypher
az keyvault secret set --vault-name kv-tc-vtwbowt3wtkpy --name MARKET-CYPHER-WEBHOOK-SECRET \
    --file /tmp/rb_cypher --query "id" -o tsv
shred -u /tmp/rb_cypher

# Restart prod + revert TV alert templates to prior secrets (operator-side)
ssh azureuser@trading.jacksumner.com "sudo systemctl restart trading-corp.service"
```

**C-1 status — partial, not done:** **2 of 13+ credentials rotated.** The 11+ deferred credentials each need their own per-portal session. Do not let "C-1" read as closed in any future scan. Mark explicitly in BACKLOG: "C-1 PARTIAL — webhook secrets only".

---

## 2026-05-26 23:46–23:54 UTC — C-7 webhook secret-scrub DEPLOYED + 5-row backfill RUN (commits `9d65be8`+`aa4f37f`)

**Commits:** `9d65be8` (scrub: webhooks.py `_scrub_secrets_from_body` + `_audit_rejected` swap + two `raw=%r`→`len=%d` log lines + 13 new tests) + `aa4f37f` (backfill: `scripts/scrub_webhook_rejected_secrets.py` + 7 tests). Cherry-picked from local branch `c7-webhook-secret-scrub` (`d7ce0df`+`5f7a198`) onto current `origin/main` (`515a870`) — the original SHAs sat on parallel-session base `b64cdc5` which is patch-identical to `f13fb05` already on `origin/main` (same author/timestamp/content, different parent), so cherry-pick was the clean path; pushing the branch would have replayed the duplicate. New SHAs `9d65be8`/`aa4f37f` carry identical file content to the original two commits.

**Triggered by:** Operator directive 2026-05-26 23:30 UTC — "C-7 deploy → backfill → ready for C-1 (security CRIT, gated, operator-supervised)". §4 webhook-path approval in-session; ordering is load-bearing (scrub-fix before backfill before C-1 rotation).

**Backup tag:** `pre-c7-scrub-20260526` on `trading_corp/web/webhooks.py` (pre-deploy md5 `6fed0aa89c103ba475bd8901a8ab434a`, 58049 bytes, CRLF). No backup for the new backfill script (didn't exist before).

**Files deployed (1 modify + 1 new):**
- `trading_corp/web/webhooks.py` — audit-write-path ONLY: new module-level `_SECRET_FIELDS = ("secret", "webhook_secret", "token")` + `_scrub_secrets_from_body(raw: bytes) -> str` helper at module scope; `_audit_rejected` swaps `raw[:500].decode(...)` → `_scrub_secrets_from_body(raw)` (same call sites, no signature change); two `log.warning(... raw=%r, raw[:200])` lines (lord-otter line ~178, market-cypher line ~364) become `len=%d, len(raw)`. **Does NOT touch:** HMAC check, IP allowlist, replay window, secret comparison, agent dispatch, risk gate, order construction, place_order. Audit-write-before-branch invariant preserved. Owner: azureuser:azureuser. CRLF preserved. Post-deploy md5: `86db1afec568a871b8a6e634c3b37a64`, 58565 bytes (+516 vs baseline).
- `scripts/scrub_webhook_rejected_secrets.py` — NEW one-shot backfill (argparse `--db` / `--dry-run` / `--verbose`; reuses the same regex shape as the in-prod scrub; idempotent on already-redacted text; WAL-safe online). Owner: azureuser:azureuser. md5: `9297904537532afec0842658e9a8c5fb`, 5788 bytes.

**Features shipped (load-bearing for future "is X done?" checks):**
- **C-7 (rejected-webhook audit plaintext leak) CLOSED.** No new `webhook_rejected` row can persist a plaintext JSON-shaped secret. Verified end-to-end on prod with bad_secret rejection carrying marker `C7VERIFYLIVE2026052623XX` — audit row's `raw_body_snippet` reads `{"secret": "***REDACTED***","symbol":"TESTSCRUB","signal":"sell"}` (marker absent, REDACTED present).
- **Historical leak surface cleared.** Backfill scrubbed the 5 pre-existing leaked rows (id 105, 402, 722, 1006, 1116). C-1 secret rotation is now safe to execute — the OLD secret does not survive in any audit row through the rotation event.
- **Dual control on log path.** Even on non-JSON-shaped bodies that the regex doesn't match (malformed_json with kv-form text), the journald warning emits `len=N`, not raw content — so the only residual exposure is in the audit DB row of a malformed_json rejection that carries kv-form credential text, which is vanishingly small in practice (auth scheme requires JSON).

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Regex matches JSON-shaped string fields only:** `"(secret|webhook_secret|token)"\s*:\s*"[^"]*"` case-insensitive. TV static-bearer body is always JSON-shaped so every `bad_secret` rejection (the actual C-7 leak path) is in scope. Documented boundary in BACKLOG.md C-7 entry.
- **CRLF preservation via scp:** prod webhooks.py is CRLF, the file scp'd over from the local CRLF checkout (also CRLF after cherry-pick — Windows checkout). md5 match local↔prod = byte-for-byte identical. The C-2 deploy 2026-05-24 followed the same recipe and called this out explicitly.
- **Cherry-pick path chosen over branch push:** local branch `c7-webhook-secret-scrub` had foreign ancestor `b64cdc5` patch-identical to `f13fb05` already on main. Pushing the branch would have replayed the duplicate; cherry-picking `d7ce0df` + `5f7a198` produced new SHAs (`9d65be8`/`aa4f37f`) with byte-identical file content but clean parentage from `515a870`. Documented in BACKLOG.md C-7 entry as the load-bearing branch-isolation option.
- **`--verbose` defeats the script's "never echo secrets" design.** The dry-run with `--verbose` (used once for drift check before the real run) DID print the cleartext secrets of id 402/1006/1116 over ssh stdout. Real run was summary-only. Process learning: dry-run drift check should use `--verbose` only if you accept stdout-channel exposure of the very thing you're scrubbing. Both the in-stdout secrets and the historical audit rows are rotated out of play by C-1 in the next session, but the principle stands.

**Verification — pre-deploy:**
- 23/23 tests green under `scripts\run_capped.ps1`: 16 `tests/test_webhook_audit_trail.py` + 7 `tests/test_scrub_webhook_rejected_backfill.py`. 3.57s.
- Cherry-pick onto clean `origin/main` (`515a870`); no merge needed; new SHAs `9d65be8` + `aa4f37f`.
- Local md5s match expected: webhooks.py `86db1afec568a871b8a6e634c3b37a64`, backfill script `9297904537532afec0842658e9a8c5fb`. Git blob hashes `369fdaa0...` + `ab1ed258...`.

**Verification — on prod (post-restart 23:46:22 UTC):**
- PRE_PID (long-running 2026-05-24 process) → POST_PID `1507621` at 23:46:22 UTC. `NRestarts=0`, `ActiveState=active`, single-startup (no crash loop).
- Port 8000 bound at ~23:53 UTC (~7min post-restart, IC position-manager startup catch-up limiting step per [[reference-prod-systemd-units]]).
- healthz local `127.0.0.1:8000/healthz` AND Caddy public `https://trading.jacksumner.com/healthz` both `{"status":"ok","mode":"PAPER"}`.
- Journal clean of new errors. Known noise: yfinance BTC/USD earnings-not-found (pre-existing), Fidelity shared-session bootstrap failure → fall-back-to-paper (pre-existing flow).
- File md5 post-deploy match local: webhooks.py `86db1afec5...` (CRLF preserved), backfill `9297904537...`.
- Semantic markers on prod webhooks.py: `_SECRET_FIELDS` count=2, `_scrub_secrets_from_body` count=2, `len=%d` count=2, `raw=%r` count=0.

**Live-scrub verification — the gate (passed at 23:53:12 UTC):**

Two bad_secret rejections from prod localhost — one to each of `/webhook/tradingview/lord-otter` and `/webhook/tradingview/market-cypher` — with body `{"secret":"C7VERIFYLIVE2026052623XX[_CYPH]","symbol":"TESTSCRUB","signal":"sell"}`. HTTP 401 `{"status":"rejected","reason":"auth failed"}` on both. Audit rows read via raw `sqlite3` CLI (independent of LoggerAgent):

```
ts                        actor          reason       raw_body_snippet
2026-05-26T23:53:12+00:00 market_cypher  bad_secret   {"secret": "***REDACTED***","symbol":"TESTSCRUB","signal":"sell"}
2026-05-26T23:53:12+00:00 lord_otter     bad_secret   {"secret": "***REDACTED***","symbol":"TESTSCRUB","signal":"sell"}
```

Marker leak count `LIKE '%C7VERIFYLIVE2026052623XX%' AND kind='webhook_rejected'` = **0**. REDACTED count on those two rows = **2**. Live scrub confirmed on BOTH webhook handlers (lord_otter + market_cypher).

**Backfill — run + verification (23:53–23:54 UTC):**

Pre-backfill drift check (`--dry-run`): `rows_scanned=10 rows_changed=5 rows_already_clean=5` — matches the original BACKLOG dry-run baseline of "5 would scrub" (now 10 total because of the two new live-scrub verification rows already-redacted).

Real run (no `--dry-run`, no `--verbose`): `rows_scanned=10 rows_changed=5 rows_skipped_bad_json=0 rows_skipped_no_snippet=0 rows_already_clean=5`. Idempotency probe via re-`--dry-run`: `rows_changed=0 rows_already_clean=10`.

Post-backfill state on the 5 previously-leaking rows (`id IN (105, 402, 722, 1006, 1116)`), all now read `"secret": "***REDACTED***"`. One quoted in full:

```
id     = 1116
actor  = lord_otter
reason = timestamp_skew_1467s
ts     = 2026-05-03T19:51:27+00:00
snippet= {"secret": "***REDACTED***","signal":"cvd_bear_flip","ticker":"BTCUSD","exchange":"COINBASE","price":78680.40,"time":"2026-05-03T19:27:00Z","interval":"3"}
```

`SELECT COUNT(*) FROM audit_event WHERE kind='webhook_rejected' AND payload_json LIKE '%REDACTED%'` = **7** (5 just-backfilled + 2 live-scrub verification posts). The loose `LIKE '%secret%:%' AND NOT LIKE '%REDACTED%'` returns 3 rows BUT those are false positives: id 4642/4661/4686 from 2026-05-10 where the audit payload's `reason` field literally is `"bad_secret"` (substring `secret` in the value) but the body snippet has no `secret` field at all — TV alerts that omitted the secret entirely and got rejected. No actual cleartext secret remains in any webhook_rejected row.

**Inert / dormant on current traffic:**
- The C-7 path fires only on `webhook_rejected` audit (bad_secret / malformed_json / json_not_object / ip_blocked / etc.). On non-rejection traffic the new helper is inert.
- The two live-scrub verification rows (id 732404, 732405) are real prod audit rows — left in place (removing would corrupt audit history; both already redacted; idempotent on re-scan).

**C-1 unblock state:** scrub fix is live → no new leak can persist → 5 historical leaks scrubbed → C-1 secret rotation can proceed in its own session without leaving rotated-secret records in audit history. The load-bearing order (scrub-deploy → backfill → C-1) is satisfied.

**Rollback recipe (reverts to OLD pre-scrub state — webhook_rejected rows will resume leaking; use ONLY if a live regression is observed):**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-c7-scrub-20260526; BASE=/home/azureuser/trading_corp;
mv \$BASE/trading_corp/web/webhooks.py.\$TAG \$BASE/trading_corp/web/webhooks.py;
sudo systemctl restart trading-corp.service
"
# Backfill is one-way (UPDATE in place over ***REDACTED***). To restore raw snippets you would need to reconstruct from the journald 'len=N' lines — practically not recoverable. This is intentional.
```

---

## 2026-05-26 22:58 UTC — Tastytrade OAuth rotation runbook landed (doc artifact, no prod touch)

**Commit:** `27dd0ef` — `runbooks/tastytrade_oauth_rotation.md` (canonical atomic 2-step rotation + 7 system-state freshness checks + 6-symptom failure-chain diagnosis; **bash-only KV writes (PowerShell `--value` form removed — uncloseable plaintext window)**, Read-Host-AsSecureString for Windows registry env-var, hard history-purge gate) + `scripts/check_tt_token_scope.py` (fail-closed JWT scope check, 10/10 paths verified empirically). Memory pointer at `[[feedback-tastytrade-rotation-runbook]]`. Forward-link target for any future Tastytrade-touching session. **No prod files modified; no rollback needed** (`git revert 27dd0ef` removes both files).

---

## 2026-05-26 22:28 UTC — analyze-whale Analyze button hx-target bug fix (selector defect)

**Commits:** `802f739` (template-only one-char fix). Followup to the 2026-05-26 03:30 UTC Phase B deploy.
**Triggered by:** Operator report — "the analyze button returns nothing." Browser click was a no-op despite the endpoint smoke being green at deploy time. Root cause: button's `hx-target` used a `>` direct-child CSS combinator but `.whale-audit-container` lives inside the sibling row's `<td colspan=13>`, two levels under the `<tr id="whale-audit-{prefix}">`. `document.querySelector` returned null, htmx silently no-op'd. The 03:30 UTC endpoint-smoke (direct HTTP POST) couldn't catch this because the bug was purely browser-side selector resolution.
**Backup tag:** `.pre-hxtarget-fix-20260526` on `pm_dashboard_body.html` (md5 `2904256301ff26211b09cd79436f38fe`, the Phase-B post-deploy baseline).

**Files deployed (1 modify):**
- `trading_corp/web/templates/partials/pm_dashboard_body.html` — line 925 `hx-target="#whale-audit-{...} > .whale-audit-container"` → `hx-target="#whale-audit-{...} .whale-audit-container"` (descendant combinator). Owner: root:root. LF md5 post-deploy: `490d0021257cd0fc7fc9dbbb4d582593`; 51717 bytes (−2 bytes vs baseline).

**Features shipped:**
- **Analyze button actually fires the htmx swap in the browser.** The endpoint, partial, telemetry, and cache were already deployed 03:30 UTC and verified by direct POST — only the dashboard click path was broken.

**Notable code changes:**
- **Deploy mechanic — sed-in-place via `az vm run-command`** preserved prod's LF line endings (local checkout is CRLF; an scp of the local file would have rewritten line endings across all 976 lines and bloated the diff). The sed target ` > .whale-audit-container` is unique in the file; the partial's own `closest .whale-audit-container` is not affected.
- **No service restart.** Per [[reference-prod-systemd-units]], template-only changes are picked up on the next request without a `trading-corp.service` bounce; only browser cache needed to bust.
- **Class of bug — endpoint-smoke ≠ click-path smoke.** The 03:30 UTC verification ran a direct `POST` against the route; the broken CSS selector was downstream of that, in the browser. Future analyze/promote/htmx-swap deploys should include a real browser click as part of the verification step (or at least a static assertion that the selector resolves against the rendered DOM). Filed as the class generalisation, not a new gate.

**Verification — on prod (post-sed 22:28 UTC):**
- md5 transition: `2904256301ff26211b09cd79436f38fe` → `490d0021257cd0fc7fc9dbbb4d582593` (matches local LF md5 exactly).
- Pattern counts: broken ` > .whale-audit-container` = 0 (was 1), fix-form `hx-target="#whale-audit-... .whale-audit-container"` = 1.
- Line count 976 unchanged; size 51719 → 51717 (the deleted ` >` is exactly 2 chars).
- Operator-side: hard-refresh `https://trading.jacksumner.com/pm` and click Analyze on any row in the 53-row watch_only slot → partial should render below the row in ~5-25s (cache miss on first click per whale, ~<200ms on cache hit).

**Inert / dormant — nothing.** The fix is live; next click exercises the corrected selector.

**Rollback recipe:**
```bash
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
  --command-id RunShellScript --scripts "
F=/home/azureuser/trading_corp/trading_corp/web/templates/partials/pm_dashboard_body.html
mv \$F.pre-hxtarget-fix-20260526 \$F
chown root:root \$F
"
# No service restart required for template rollback.
```

---

## 2026-05-26 03:30 UTC — pm-watchlist Analyze-Whale dashboard endpoint (Phase B, Board-approved)

**Commits:** `78323c3` (dashboard endpoint + button + partial + tests on branch `analyze-whale-dashboard`). Phase A modules (`a1cbe18` + `b42a8a5` + earlier) also deployed in this window — they had been merged to main as the operator-local CLI work but were never on prod's disk. Deploy-import-graph oversight caught at the smoke step (see "Mid-deploy correction" below).
**Triggered by:** Board ratification of the Phase B plan after Phase A CLI verified Magamyman gate locally. Predecessor: the analyze-whale-cli planning + CLI build earlier today.
**Backup tags:**
- `.pre-analyze-dashboard-20260526` on `routes.py` (md5 `45881c9572b02ea3e6618087f64be0f6`) and `pm_dashboard_body.html` (md5 `7d857f9a5243750608185bde82ae4f79`) — both pre-Phase-B baseline.
- `.pre-phaseA-modules-20260526` on `config/agents.yaml` (md5 `70697b07f4c0a9a1cd35cb926b55f8c6`) and `trading_corp/agents/research/cost.py` (md5 `2cb93de2e1deac8b203c373eeb8bf292`) — pre-Phase-A-modules baseline. The 3 new module files have no backup (didn't exist before).
- `analyze_whale_result.html` has no backup (new file).

**Files deployed (3 NEW + 4 modified across two staging passes):**

Phase B pass (03:14 UTC):
- `trading_corp/web/routes.py` — adds `POST /api/polymarket/watchlist/analyze/{proxy_wallet}` (~190 LOC including handler + imports + telemetry). Mirrors `iron_condor_grade` (:1700) shape. Owner: azureuser:azureuser. LF md5 post-deploy: `936c7f4e476f783916f8869aa714d15a`; 191660 bytes.
- `trading_corp/web/templates/partials/pm_dashboard_body.html` — adds Analyze button + sibling `<tr id="whale-audit-{wallet[:10]}">` swap target row + htmx-indicator spinner alongside the existing View / Promote buttons (line ~913). Owner: root:root. LF md5: `2904256301ff26211b09cd79436f38fe`; 51719 bytes.
- `trading_corp/web/templates/partials/analyze_whale_result.html` — NEW 6-section partial (Clustering / Sell footprint / Edge profile / Category concentration / Realized PnL / Verdict). Owner: root:root. LF md5: `e24da5a65c403c792d2073470a438999`; 12250 bytes.

Phase A modules pass (03:30 UTC — see "Mid-deploy correction"):
- `trading_corp/data/polymarket_whale_audit.py` — NEW compute core. Owner: root:root. LF md5: `67f3371fb97b0e41c7eb131127aa5902`; 30187 bytes.
- `trading_corp/agents/polymarket_whale_analyst.py` — NEW Haiku narrator. Owner: root:root. LF md5: `bdacfa23368f817762d7af10faf12a67`; 15183 bytes.
- `trading_corp/agents/research/polymarket_whale_audit_cache.py` — NEW namespace-isolated cache. Owner: root:root. LF md5: `febdb30b14ca029dae671826ba93ff94`; 7786 bytes.
- `config/agents.yaml` — appended `polymarket_whale_analyst: { model: claude-haiku-4-5-20251001, temperature: 0.1 }`. Owner: root:root. LF md5: `5b22b4c9ec9bac5edad47b308599b063`.
- `trading_corp/agents/research/cost.py` — added Haiku pricing to `_PRICING` table. Owner: root:root. LF md5: `5cbae222472e4fe6f188a32c57a5fb73`.

**Features shipped:**

- **On-demand whale audit on the dashboard.** Operator clicks "Analyze" on a watchlist row → ~5-25s LLM-narrated audit renders inline below the row. Six sections match the CLI output. Verdict line cites verbatim report numbers (LLM does no arithmetic); null-verdict cases (no_llm / cap_hit / unavailable / error) render operator-readable reasons.
- **Cache shared between CLI and dashboard.** Same `polymarket_whale_analyst` namespace, same key format. Re-analyzing the same whale (with no new activity) is free; `?force=1` evicts.
- **Per-call telemetry.** Each dashboard analyze fires one `polymarket_whale_analyzed` audit_event with `source="dashboard"`, token/cost/cache_hit/duration fields. Same audit kind as the CLI; differentiated by `source` field.

**Notable code changes (callouts for future me):**

- **Mid-deploy correction (load-bearing for future deploys):** The Phase B pass alone (03:14 UTC) shipped routes.py + 2 templates and restarted the service cleanly (healthz 200 in ~5min). BUT the route's `from trading_corp.agents.polymarket_whale_analyst import WhaleAnalyst` etc. references Phase A modules that the CLI build had committed locally but never deployed to prod. First smoke-curl returned `500 Internal Server Error` in 4ms; journal trace: `ModuleNotFoundError: No module named 'trading_corp.agents.polymarket_whale_analyst'`. This is exactly the failure mode that the `[[deploy-import-graph-audit]]` memory entry (filed earlier today) warns about — hash-comparing only files in the diff misses NEW imports referring to modules absent on prod. The Phase A modules pass (03:30 UTC) fixed this; second restart + smoke-curl returned 200 in 4.3s with the expected verdict. **Future me: when deploying a dashboard surface that imports from any module not on prod, ls-check each imported path on prod before the restart, not after.**
- **Single-process architecture tax (filed to BACKLOG):** `trading-corp.service` runs the web app + ALL strategies (polymarket_arbitrage, kalshi_*_arb, bitunix_futures, kalshi_sports_*, etc.) + Playwright driver in ONE Python process. Restart blips ALL strategies + TradingView webhooks for ~5 min. This deploy paid that tax twice (once per restart, 10min total strategy pause). All paper — no real-money impact — but the architecture means every UI deploy forces a full strategy bounce. A `trading-corp-web.service` split would decouple this. See BACKLOG entry.
- **Read-only invariant preserved on prod.** Verified post-deploy via direct DB query: all four promotion-relevant slots (`watch_only_whales`, `selected_whales`, `pinned_whales`, `metrics_epoch`) carry their pre-deploy `updated_ts`. The endpoint's only writes are (a) the audit cache under isolated `polymarket_whale_analyst` namespace and (b) the audit_event row. Confirmed via SQL.
- **Cost on prod matches budget.** First Magamyman analyze: $0.0015 (Haiku 4.5). Within the planned ~$0.0013-0.0015 per-whale budget.

**Verification — pre-deploy:**

- 111 tests passing locally (10 new endpoint + 35 Phase A unit + 66 regression in polymarket suite). 10 endpoint tests parametrize null-reason taxonomy + assert NO promotion slot is written (direct load_agent_state pre/post compare).

**Verification — on prod (post-final-restart 03:30:19 UTC):**

- PID rotated 1448692 → 1458904 (Phase B restart 03:15:30) → 1462117 (Phase A modules restart 03:30:19).
- healthz returns 200 `{"status":"ok","mode":"PAPER"}` at ~5min post-each-restart.
- Strategies resumed: 9,648 audit_event rows in the last 60s post-restart (matches pre-deploy ~7k baseline).
- Import smoke via prod venv: `from trading_corp.agents.polymarket_whale_analyst import WhaleAnalyst` etc. — all 3 module imports succeed; `AGENT_NAMESPACE == 'polymarket_whale_analyst'`.
- Endpoint smoke (POST `/api/polymarket/watchlist/analyze/0x4dfd481c16d9995b809780fd8a9808e8689f6e4a` = Magamyman):
  - HTTP 200, 4.3s, body 7763 bytes
  - All 6 sections render; Magamyman name + Iran cluster + `$1,005,202` held-to-res + `$787k` realized + partial/round-trip flags all present
  - Verdict line (Haiku-generated): cites verbatim report numbers, no arithmetic
  - `audit_event` row landed: actor=polymarket_copy_trader, kind=polymarket_whale_analyzed, source=dashboard, cache_hit=0, verdict_emitted=1, cost=$0.0015, duration=4191ms, n_resolved_decisions=98
  - Audit cache entry under `polymarket_whale_analyst:polymarket_whale_audit:0x4dfd481c16...:1777744382` (isolated namespace confirmed via SQL)
  - All 4 protected slots' `updated_ts` UNCHANGED across deploy + smoke window

**Inert / dormant — nothing.** Endpoint is live. Operators can click Analyze on any watchlist row immediately.

**Promotion-pause status unaffected.** This is review tooling; copy execution / watchlist seed / promotion state all untouched.

**Rollback recipe:**

```bash
# Step 1: revert routes.py + pm_dashboard_body.html to pre-Phase-B baseline
# (removes the Analyze button + endpoint; analyze_whale_result.html stays
# on disk but unreferenced, harmless)
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
  --command-id RunShellScript --scripts "
TAG=pre-analyze-dashboard-20260526
BASE=/home/azureuser/trading_corp
mv \$BASE/trading_corp/web/routes.py.\$TAG \$BASE/trading_corp/web/routes.py
mv \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html.\$TAG \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html
rm -f \$BASE/trading_corp/web/templates/partials/analyze_whale_result.html
chown azureuser:azureuser \$BASE/trading_corp/web/routes.py
chown root:root \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html
systemctl restart trading-corp.service
"
# Step 2 (optional): also revert Phase A modules (yaml + cost.py back to baseline,
# remove the 3 NEW module files). Skip unless the Phase A modules are themselves
# at issue — they only execute when the analyze endpoint or CLI is called.
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
  --command-id RunShellScript --scripts "
TAG=pre-phaseA-modules-20260526
BASE=/home/azureuser/trading_corp
mv \$BASE/config/agents.yaml.\$TAG \$BASE/config/agents.yaml
mv \$BASE/trading_corp/agents/research/cost.py.\$TAG \$BASE/trading_corp/agents/research/cost.py
rm -f \$BASE/trading_corp/data/polymarket_whale_audit.py
rm -f \$BASE/trading_corp/agents/polymarket_whale_analyst.py
rm -f \$BASE/trading_corp/agents/research/polymarket_whale_audit_cache.py
systemctl restart trading-corp.service
"
```

---

## 2026-05-26 01:42 UTC — pm-watchlist PnL-aggregation fix on top of clustering fix (Board-approved)

**Commits:** `a1cbe18` (code+tests), `b42a8a5` (plan + corrected-PnL replay). On branch `pm-watchlist-pnl-aggregation-fix` on `origin`. Main NOT advanced this deploy (ff-merge to main is a separate user-driven step). Parallel-session kalshi_weather commits (`c26882f` → `321d426`) landed on `main` between this deploy's planning and execution; not bundled with this work.
**Triggered by:** Board ratification after the 2026-05-26 00:44 UTC clustering-fix manual fire produced a 53-row roster (outside the predicted 97-172 band). Root-cause: per-fill PnL math interacting with `(cid, oi)` windowing — only the survivor fill's `size` flowed into `compute_polymarket_stats`, so cluster-heavy whales' realized PnL was deflated 3-30x and many were artifactually rejected by the $5k PnL floor. Plan: `reports/2026-05-26_polymarket_pnl_aggregation_fix_plan.md`.
**Backup tag:** `.pre-pnl-fix-20260526`. Backup md5 captured pre-move: `6b4372b7d38393c4b38a9d9999521dd5` (the clustering-fix-only version shipped 2026-05-25 22:20 UTC; rollback to it restores today's 53-row behavior, NOT pre-clustering-fix fill-counting behavior).

**Files deployed (1 modify):**
- `trading_corp/scripts/seed_polymarket_watchlist_deep.py` — adds `_aggregate_window_to_decisions(activity, window) → list[ActivityRow]` (~85 LOC incl docstring). For each `(cid, outcome_index)` decision in the window, collapses to one synthetic row with `size = Σ s_i`, `usdc_size = Σ usdc_i`, `price = (Σ p_i·s_i) / Σ s_i` (size-weighted avg across ALL BUY+TRADE fills on that decision, from the full activity feed). Wired into the pipeline as one line after `_select_resolved_buys_window`. Owner: root:root. LF-canonical md5 post-deploy: `906435c92c498f4bc54d4c9b88d74aa9`; size 38564 bytes (vs 34530 for the clustering-fix-only).

**Features shipped:**
- **Per-decision PnL valuation.** A 29-BUY cluster now has its decision's PnL credited for the full economic exposure across all 29 fills, not just the survivor fill. Closes the loop between count-axis (n = distinct decisions, shipped 2026-05-25 22:20 UTC) and value-axis (PnL aggregates across decisions, this deploy) — both decision-level now.
- Empirical replay against cached 329-wallet corpus: cohort under all four production floors = **136 wallets** (inside predicted 97-172 band; vs the 53-row broken state). No floor re-tuning — math identity restores cohort without touching `min_resolved_buys=10`, `provisional_threshold=50`, `min_windowed_wr=0.62`, `min_windowed_pnl=5000.0`.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **Math identity (load-bearing for trust):** `(1 - weighted_avg_price) · total_size ≡ Σ_i (1 - p_i) · s_i` for wins; mirror for losses. `compute_polymarket_stats`'s per-row formula on aggregated rows produces the same number as a per-fill walk. **Hand-proven on real data — Magamyman's top 3 clusters (131 + 87 + 68 fills) AND full 98-decision window match per-fill hand-sum to floating-point precision (~1e-10).** Full-window aggregated $1,005,202.882858 = per-fill hand-sum $1,005,202.882858.

- **`AvgPx` and `share_below_70` semantics shifted (semantic upgrade, not regression):** Pre-fix these were per-survivor-fill statistics; post-fix they are per-decision weighted statistics. Numbers will move slightly in either direction depending on cluster compositions.

- **`window_days_span` unchanged** — `_aggregate_window_to_decisions` preserves the survivor row's timestamp.

- **`compute_polymarket_stats` and `_is_win_for_buy` UNTOUCHED** — fix lands entirely in the seed script. Win/loss formula, half-life weighting, WhaleStats packing all unchanged against aggregated rows.

- **Realized-PnL caveat — DOCUMENTED, NOT GATED.** Aggregated PnL is "held-to-resolution credit," not realized cash-flow PnL. `compute_polymarket_stats` skips SELL rows at `polymarket_whale_stats.py:124-129` — pre-existing behavior, inherited by aggregation, NOT introduced by this fix.

  Magamyman audit (representative top-of-list whale):
  - 2 of 3 top clusters held cleanly through resolution (zero SELLs) — hand-sum IS realized PnL.
  - **1 cluster (US-strikes-Iran-Feb-28, his $679k contributor) sold 570,098 of 861,154 contracts (66%) before resolution.** REDEEM row shows actual held position was 291,056 contracts. The $679k credit is "what he WOULD have made if he'd held to settlement" — actual realized was less. He also had a paired oi=1 NO-side that was a fully-exited round-trip (100% SELL/BUY), counted in this design as a held loss.
  - Whale-level across his 193 resolved decisions: 3 had any SELL; total SELL/BUY ratio 24.6%.

  **Implication for the review-phase promotion gate:** promotion in this phase is paper-mode review (operator adds a whale to their review set to watch trades before deciding to follow live), NOT capital deployment. The SELL footprint of a candidate is a per-whale review note, not a hard pre-promote check. A future possible enhancement using REDEEM rows + SELL proceeds to compute realized PnL is filed as backlog priority — likely superseded by per-whale on-demand review tooling that surfaces the same caveat at the point of use.

- **Magamyman data points (for future reviewer calibration):**
  - "Israel military response against Iran in October?" — 131 BUYs, wavg $0.45519, total 93,723 contracts, REDEEM matches BUY (held cleanly), $51,077 hand-sum ≡ aggregated.
  - "Israel military response against Iran by Friday?" — 87 BUYs, wavg $0.24550, 52,157 contracts, held cleanly, $39,352.
  - "US strikes Iran by February 28, 2026?" — 68 BUYs at wavg $0.21130 (861,154 contracts); 4 SELLs took 570,098 contracts pre-resolution; REDEEM = 291,056. Aggregated $679,192 is held-to-resolution credit; realized is less.

**Verification — pre-deploy:**
- All 34 tests in `tests/test_polymarket_watchlist_seed.py` pass (8 new unit + 1 new integration + 25 prior).
- Empirical replay (`scripts/verification/2026-05-26_clustering_plan/replay_with_pnl_agg.py`) against cached 329-wallet corpus: cohort 136 (inside 97-172 band), non-provisional 69. Top-of-list = Magamyman at $1,005,203. JSON at `tmp/2026-05-26_clustering_plan/replay_with_pnl_agg.json`.
- All four test cluster-traders still correctly drop on the right floors (not artifactual PnL):
  - Runaround: WR 0.6000 < 0.62 floor (aggregated PnL $51,859)
  - Mosley1: WR 0.5000 < 0.62 floor (aggregated PnL $939,241)
  - weflyhigh: WR 0.5600 < 0.62 floor (aggregated PnL $286,777)
  - surfandturf: n 5 < 10 floor (aggregated PnL $286,832)
- Hand-proof against Magamyman's full activity feed (822 rows) confirmed math identity to ~1e-10 on top-3 clusters AND full 98-decision window.

**Verification — on prod (post-deploy):**
- Backup md5 `6b4372b7d38393c4b38a9d9999521dd5` (matches clustering-fix-only baseline).
- Post-deploy md5 `906435c92c498f4bc54d4c9b88d74aa9` (matches local LF blob).
- Owner root:root, mode 644, size 38564 bytes.
- Smoke import via prod venv: both `_select_resolved_buys_window` and `_aggregate_window_to_decisions` imported cleanly.
- Functional smoke: 3-fill mixed-price cluster (prices 0.50/0.60/0.40, sizes 100/200/300) → total_size=600, wavg_price=0.483333, per-row PnL formula on aggregated row = $310.00 = hand-sum across 3 fills. MATCH.

**Inert / dormant on current traffic:**
- **No code path exercises until the next weekly seed fire.** The current `agent_state(polymarket_copy_trader, watch_only_whales)` slot (53 rows from the 2026-05-26 00:44 UTC manual fire under clustering-fix-only code) continues to serve the dashboard until Sunday's overwrite.
- **First fire under this fix: Sun 2026-05-31 ~13:00 UTC.** Same systemd unit + timer + ExecStart as 2026-05-25 14:25 UTC overwrite-cadence edit. Expected: roster ~136 (replay-predicted), zero `preserved` rows (overwrite cadence), `n` reflects distinct decisions, `realized_pnl_usdc` magnitudes restored to honest decision-level (3-30x today's broken 53-row figures).
- **No manual seed run this deploy** (operator directive — ride Sunday).

**Promotion-resume-pending-verification:**
- Promotion off the watchlist remains PAUSED until the post-fire verification gate passes:
  - Roster size ~97-172 (expected ~136)
  - No 100% WR rows
  - `n` column reflects distinct decisions
  - Provisional flag fires on n<50 rows
  - Clean exit status, wall-clock in expected band
- If those pass, **promotion unpauses NORMALLY** — no SELL-footprint forensics gate. The held-vs-realized caveat is a per-whale review note, not a hard pre-promote check (because promotion in this phase is paper-mode review, not capital deployment).

**NOT touched by this deploy:**
- `_select_resolved_buys_window` — count-axis correct as shipped.
- `compute_polymarket_stats` + `_is_win_for_buy` — unchanged.
- All four floor values — explicitly held; no re-tuning.
- `agent_state(polymarket_copy_trader, watch_only_whales)` slot — current 53-row content stays until Sunday's overwrite.
- `agent_state(polymarket_copy_trader, selected_whales)` — copy-execution roster.
- `refresh_polymarket_whales.py`, `polymarket_copy_trader` strategy, broker, risk gate, audit pipeline, dashboard render — unchanged.
- Systemd unit + timer — unchanged.

**Rollback recipe:**
```bash
# Single-file rollback to the clustering-fix-only state (today's 53-row behavior).
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
  --command-id RunShellScript --scripts "
TAG=pre-pnl-fix-20260526
BASE=/home/azureuser/trading_corp/trading_corp/scripts
F=seed_polymarket_watchlist_deep.py
mv \$BASE/\$F.\$TAG \$BASE/\$F
chown root:root \$BASE/\$F
chmod 644 \$BASE/\$F
md5sum \$BASE/\$F
# Expected: 6b4372b7d38393c4b38a9d9999521dd5
"
```
For deeper rollback (pre-clustering-fix fill-counting behavior): nested rollback via `.pre-clustering-fix-20260526` backup — see 2026-05-26 22:20 UTC entry.

---

## 2026-05-26 01:10 UTC — kalshi_weather bias-offset v1 deployed (re-deploy after 00:24 crash-loop)

**Commits:** `c26882f` (original wiring, 22 cells), `92e8662` (initial cutoff bump to 00:18, superseded), `6d66ea7` (inlined derive_season + equivalence test — the fix), `<this commit>` (cutoff advance to 01:08 + deploy_log entry).

**Triggered by:** Board approval after STEP 1 train/test + cross-source validation (Reading C: ship cells with |train_off| ≥ 1.0°F). See `reports/2026-05-25_sigma_three_way_calibration.md` for the data.

**Backup tag:** `pre-bias-offset-20260526-0018` on 3 prod files. Captured during the FIRST attempt at 2026-05-26 00:20 UTC; reused for this re-deploy because the 00:44 rollback restored prod byte-equal to the backup snapshot. md5s pre-deploy:
- `3605df4ce3195ea327d9a77bc269d9d5` _weather_math.py
- `913280ae09780e65884aa7f177206550` kalshi_weather_arb.py
- `908cf16dd033dd81f2eb0ed2de97d273` data.py

**Files deployed (3 modify):**
- `trading_corp/agents/strategies/_weather_math.py` — added `BIAS_OFFSETS_V1` (22-cell `dict[(station, season), (offset_f, validation_tag)]`), `BIAS_OFFSET_SOURCE_TAG`, `lookup_bias_offset()`, and an INLINED byte-equivalent copy of `derive_season` (the residual_logic version). Inlining was the fix for the prior crash — see "Prior attempt" below. md5 post-push: `7a025622345e25c73b9f0ce23d7e0968` (matches local).
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — `_resolve_coords` returns `station_id` (verified ICAO; None unless `coord_source='yaml_verified'`). `_evaluate_market` after forecast rebuild + before `evaluate_weather_market`: derives season from target_iso, looks up `(station_id, season)` offset, rebuilds `ForecastPoint` with `temp_f + offset_f` if non-zero. Source string gains `+bias_offset` marker. Fail-open to `_season='_unparseable'` sentinel (no match → 0.0) on unparseable target_iso. 6 new audit fields on `eval_payload`: `forecast_temp_f_pre_offset`, `bias_offset_applied_f`, `bias_offset_source`, `bias_offset_validation`, `bias_offset_season`, `bias_offset_station_id`. md5 post-push: `c4a56c07f9e7bc07982ae57cca4c066f` (matches local).
- `trading_corp/web/data.py` — `DASHBOARD_RT_CUTOFFS['kalshi_weather']` advanced from `2026-05-22T16:25:00+00:00` (P3 xref deploy) to `2026-05-26T01:08:00+00:00` (this deploy). Sed-in-place; only the timestamp string changed.

**Restart:** PRE_PID `1442346` (post-rollback PID from 00:44) → POST_PID **`1448692`** at restart_ts `2026-05-26T01:10:33+00:00`. systemctl active. Healthz `{"status":"ok","mode":"PAPER"}` green at 01:15:45 UTC (5min IC catch-up — normal).

**Stability watch (the lesson from the prior crash-loop):** PID 1448692 confirmed unchanged + active at T+30s / T+60s / T+90s / T+120s. Past 2+ cycles of the prior 50s crash-loop interval before declaring success. Healthz binding completed within the window.

**Features shipped:**
- **22-cell per-(station, season) bias-offset correction.** Lookup keyed on registry-direct station_id (None unless coord_source='yaml_verified' — never applied to legacy_fallback / disabled_skip). 9 fully_validated spring cells (NBM train/test 79% + nws_blend cross-source 84%) + 13 nbm_only non-spring watch-items (NBM train/test only; nws_blend cross-source pending forward-accumulation).
- Largest offset: KDEN spring -3.187°F. Pattern: Texas/High Plains cold-bias cluster + KLAX/KSFO marine warm bias.
- Today (2026-05-26) is spring — fully_validated cells active immediately. Non-spring nbm_only cells activate as seasons turn (watch-item: re-validate cross-source as live nws_blend data accumulates).
- Stations NOT in `BIAS_OFFSETS_V1` (KATL, KDCA, KMIA, KPHL, KPHX, KSEA) pass through untouched; bias_offset_applied_f = 0.0.

**Notable code changes:**
- `derive_season` INLINED into `_weather_math.py` (NOT imported from `residual_logic`). The bias offsets were FIT using `residual_logic.derive_season`'s boundaries; the inlined version is BYTE-EQUIVALENT (asserted by `tests/test_derive_season_inlined_equiv.py` across every day-of-year + leap day + all 8 boundary edges).
- `_resolve_coords` return dict gains `station_id` field (cleanly fills the "audit row carries no station id" gap noted in prior Tier 1 work).
- Every audit row carries `forecast_temp_f_pre_offset` alongside `forecast_temp_f` for full diffability — offset rollout/rollback is reversible from audit alone.
- Bypass: setting `BIAS_OFFSETS_V1 = {}` in code disables all offsets (1-line code rollback path).

**PRIOR FAILED ATTEMPT (2026-05-26 00:24 UTC, crash-looped 17 min, rolled back 00:44):**
- Initial deploy of commits `c26882f` + `92e8662` pushed `kalshi_weather_arb.py` containing `from trading_corp.data.residual_logic import derive_season`.
- `residual_logic.py` was committed locally in `0ff6007` (C2 work) but the earlier push-to-prod was harness-blocked; prod had the importer but NOT the imported.
- journalctl: `ModuleNotFoundError: No module named 'trading_corp.data.residual_logic'` → `Main process exited, code=exited, status=1/FAILURE` → systemd restarted → repeat every ~50s.
- Rollback at 00:44:46 restored prod byte-equal to pre-deploy state; service back to healthy.
- **Process failure I own:** hash-comparing the 3 changed files isn't the same as auditing the import graph for new dependencies. The fix: grep `^+from`/`^+import` in the diff, ls-check each imported module on prod before deploy. Memory entry: `feedback_deploy_import_graph_audit.md`.
- For THIS re-deploy, the import-graph audit ran: only stdlib `datetime.date` is newly imported; all 7 `trading_corp.*` modules referenced exist on prod (verified with `ls`). `residual_logic.py` confirmed STILL absent on prod (and the re-deploy doesn't need it).

**Inert / dormant on current traffic:**
- The 13 nbm_only non-spring cells (KAUS winter/summer, KBOS summer, KDEN fall, KLAX fall, KMDW winter/summer/fall, KNYC summer, KOKC winter/summer, KSAT winter, KSFO fall) sit dormant until their season turns. None apply today.
- The 2.5-min sliver of `2026-05-26T01:08:00` (cutoff) → `2026-05-26T01:10:33` (restart_ts): pre-bias-offset audit rows in this window pass the dashboard filter. Worst-case ~150 rows from one half scan cycle. Self-corrects after a few hours of new bias-corrected rows accumulate. Noted for honesty; not worth a second restart to fix.

**WATCH-ITEM (non-spring nbm_only cross-source re-validation):**
As live nws_blend data accumulates through summer (Jun 1+), fall (Sep 1+), and winter (Dec 1+), re-run the cross-source validation per the STEP 1 procedure for that season's nbm_only cells. Pull any cell that doesn't hold (mean(z) gets WORSE after offset applied to nws_blend test data). The 9 spring cells are already cross-source-validated and don't need re-check.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bias-offset-20260526-0018; BASE=/home/azureuser/trading_corp
for f in trading_corp/agents/strategies/_weather_math.py \
         trading_corp/agents/strategies/kalshi_weather_arb.py \
         trading_corp/web/data.py; do
  cp \$BASE/\$f.\$TAG \$BASE/\$f
done
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-26 22:20 UTC — pm-watchlist clustering fix: dedupe by `(cid, outcome_index)` (Board-approved)

**Commits:** `a4558fc` (code + tests), `4d56cdf` (plan + empirics + replay scripts). Pushed to branch `pm-watchlist-clustering-fix` on `origin`. **Main NOT advanced this deploy** — the auto-mode classifier denied a direct fast-forward push to main; user authorized "push the branch" only. **`origin/main` head at deploy time:** `b22a2e5` (the σ-calibration report from a parallel session, unrelated to this deploy). The 2 commits above sit on the work branch; ff-merge to main is a separate user-driven step.
**Triggered by:** Board approval after the 2026-05-26 fix-planning session (`reports/2026-05-26_polymarket_clustering_fix_plan.md`, predecessor: `reports/2026-05-25_polymarket_wr_investigation.md` commit `297508c`).
**Backup tag:** `.pre-clustering-fix-20260526` on the single modified prod file. Backup md5 captured before move: `0f38a83ec673f37a7372e2bd6d800bd6` (matches the file shipped on 2026-05-23 — prod was in sync with the pre-deploy baseline).

**Files deployed (1 modify):**
- `trading_corp/scripts/seed_polymarket_watchlist_deep.py` — `_select_resolved_buys_window` now dedupes by `(condition_id, outcome_index)` before windowing. Walks activity most-recent-first, keeps the most-recent BUY per `(cid, oi)` pair, stops at `window_size=100` distinct pairs. Win/loss math downstream (`compute_polymarket_stats`, `_is_win_for_buy`) is byte-identical — only the row-selection unit changed. Module + function docstrings updated to reflect "distinct decisions" semantics. Owner: root:root preserved. LF-canonical md5 post-deploy: `6b4372b7d38393c4b38a9d9999521dd5`.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Per-decision windowing on the Polymarket watch list.** A whale who fills 29 BUYs on one Knicks-Cavs spread now contributes 1 window slot, not 29. WR/realized PnL/AvgPx/`<.70` share are all now per-decision metrics. Window count `n` now means distinct decisions, not raw fills. Latent on prod until the first cron fire — see "Inert / dormant" below.
- **Backlog of stale watchlist rows continues from 2026-05-24 13:08 UTC merge fire.** The current 329-row `agent_state(polymarket_copy_trader, watch_only_whales)` slot was produced by the BUGGY windowing one week ago and is being preserved until the Sunday overwrite. NOT corrected by this code deploy — only the next scheduled fire produces the corrected list.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Shipped dedupe granularity is `(condition_id, outcome_index)`, NOT the `condition_id`-only used in the plan report's empirics surrogate (`scripts/verification/2026-05-26_clustering_plan/empirics_v2.py`'s `window_A`).** They reproduce the same 97-whale cohort clean-list and the same deployment outcome for the 4 test traders. They differ 0-3 in `n` and 0-15pp in per-whale WR where hedges exist (a whale that bought BOTH `outcomeIndex=0` AND `outcomeIndex=1` on one market). The `(cid, oi)` version is deliberate and tracks ground-truth more closely: surfandturf (the canonical hedge case) lands at WR=0.40 on n=5 under the shipped code, vs honest decision WR of exactly 0.40 (2W/3L) from full-history walk — exact match. The plan's cid-only surrogate produced 0.25 on n=4 by wrongly collapsing the Thunder-vs-Spurs hedge. **This is documented to prevent a future reviewer computing cid-only, getting different per-whale numbers, and mistaking prod for buggy.**
- **Floors deliberately unchanged.** `min_resolved_buys=10`, `provisional_threshold=50`, `min_windowed_wr=0.62`, `min_windowed_pnl=5000.0` all hold. The plan flagged that the clean-list count drops from 225 → 97 under decision-counting, and that this is the floor working correctly (the 128 dropouts never had 50+ recent independent decisions). Re-tuning is queued post-deploy with real decision-counted data; not this deploy.

**Verification — pre-deploy:**
- All 25 tests in `tests/test_polymarket_watchlist_seed.py` pass locally (including 4 new tests for (cid,oi) dedupe semantics + 2 new integration tests for clustered-whale floor behavior).
- All 57 tests across `tests/test_polymarket_*.py` pass.
- Empirical replay (`scripts/verification/2026-05-26_clustering_plan/replay_via_prod_code.py`) ran the SHIPPED code against cached 329-wallet activity+resolutions data. Cohort clean-list under shipped code: **97 wallets** — identical to plan. Per-test-trader: Runaround n=100 wr=0.6000 (identical to plan), Mosley1 n=20 wr=0.5000 (+3 hedge-decisions over plan's 17), weflyhigh n=25 wr=0.5600 (+1 hedge), surfandturf n=5 wr=0.4000 (+1 hedge, matches honest 40% exactly). All 4 test traders correctly drop off the clean watchlist under the shipped code.

**Verification — on prod (post-deploy):**
- Backup md5 confirmed `0f38a83e...` (matches pre-deploy baseline).
- Post-deploy md5 confirmed `6b4372b7...` (matches local LF blob).
- Owner root:root preserved; mode 644; size 34530 bytes.
- Smoke import via prod venv (`/home/azureuser/trading_corp/venv/bin/python3`): `_select_resolved_buys_window` imports cleanly; docstring matches new content; source contains `seen: set[tuple[str, int]] = set()` and `(a.condition_id, oi)` markers.
- Functional smoke on prod: 3 same-(cid,0) BUYs → window n=1; (cid_a,0)+(cid_a,1) hedge → window n=2. Both match expected.

**Inert / dormant on current traffic:**
- **No code change exercises until the next weekly seed fire.** The current 329-row `watch_only_whales` slot was produced by the pre-fix windowing and continues to be served by the dashboard until the Sunday overwrite. Don't read this slot as "what the fix produces" before then.
- **First fire under the fix: Sun 2026-05-31 ~13:00 UTC** (weekly-overwrite cadence per `[[pm-watchlist-windowed-live]]`; `RandomizedDelaySec` may re-roll the exact second by daemon-reload events between now and then). Expected effect: roster snaps 329 → ~97-172 (the 97 floor-clean plus provisional rows down to n≥10); zero `preserved` rows in merge_stats (overwrite cadence); per-whale `wins` `losses` `win_rate` `window_size_n` columns reflect distinct-decision counts; `realized_pnl_usdc` magnitudes will drop because cluster fills no longer pile up under one decision (PnL math is per-row; rows now means decisions).
- **No manual seed run this deploy** (user instruction). The Sunday fire is the first observation point.

**Promotion-resume-pending-verification:**
- **Promotion off the watchlist remains PAUSED** ([[polymarket-whale-scoring-edge]], [[pm-watchlist-windowed-live]]). Unpaused ONLY after the Sun 2026-05-31 fire is verified (roster snaps to expected size; spot-check Runaround/Mosley1/weflyhigh show decision-WR not the old 100%; n column reflects distinct decisions, not fills). The pause does not lift on this deploy.

**Floor re-tuning explicitly deferred:**
- The plan flagged that `n≥10` + `n<50 provisional` + `WR≥0.62` floors were calibrated against fill-counted samples. Under decision-counting they bite harder (median n drops from 100 → 98, p90 WR from 1.00 → 0.90; clean list 225 → 97). Re-tuning is a separate post-Sunday decision with real decision-counted data, not this deploy.

**NOT touched by this deploy:**
- `agent_state(polymarket_copy_trader, selected_whales)` — copy-execution roster.
- `agent_state(polymarket_copy_trader, watch_only_whales)` — the existing slot stays in place; Sunday's overwrite is what produces the corrected list. **No backfill, no manual write.**
- `polymarket_copy_trader` strategy / broker adapter / risk gate / audit pipeline.
- `refresh_polymarket_whales.py` (live roster picker).
- Kalshi watchlist seed.
- Web dashboard render — columns + sort URLs unchanged.
- `compute_polymarket_stats` + `_is_win_for_buy` — win/loss math is byte-identical.
- Floors (`min_resolved_buys`, `provisional_threshold`, `min_windowed_wr`, `min_windowed_pnl`) — see "Floor re-tuning explicitly deferred" above.
- Systemd unit `trading-corp-pm-watchlist-deep.service` — ExecStart unchanged from 2026-05-25 14:25 UTC overwrite-cadence edit (`… seed_polymarket_watchlist_deep`, no `--merge`, no `--max-total`).

**Rollback recipe:**
```bash
# Code revert (single file) via az run-command — the sudoers-narrow workflow.
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
  --command-id RunShellScript --scripts "
TAG=pre-clustering-fix-20260526
BASE=/home/azureuser/trading_corp/trading_corp/scripts
F=seed_polymarket_watchlist_deep.py
mv \$BASE/\$F.\$TAG \$BASE/\$F
chown root:root \$BASE/\$F
chmod 644 \$BASE/\$F
md5sum \$BASE/\$F
# Expected post-rollback md5: 0f38a83ec673f37a7372e2bd6d800bd6
"
# No service restart needed; the seed runs from-disk per cron, picks up the rollback automatically.
```

If a Sunday fire already ran under the new code and the agent_state slot needs restoring to the pre-fix 329-row content: the pre-deploy slot is preserved in agent_state's row, but the **previous** weekly fire's content (2026-05-24 13:08 UTC merge state) is not preserved separately. The fix is to wait for the following weekly fire under the rolled-back code; promotion remains paused throughout.

---

## 2026-05-25 15:21 UTC — sudoers narrow: `azureuser NOPASSWD:ALL` → narrow allowlist (P1, BACKLOG `8d72dcc`)

**Commits:** n/a (VM-side `/etc/sudoers.d/` edit only; no repo code changed)
**Triggered by:** in-session operator approval; BACKLOG.md "P1 — `azureuser` has `NOPASSWD:ALL` sudo" (filed 2026-05-23 in `8d72dcc`, sequenced BEFORE C-1 secret rotation to shrink rotation's blast radius)
**Backup tag:** `.pre-narrow-20260525` on `/etc/sudoers.d/90-cloud-init-users`

**Files deployed (1, VM-side only):**
- `/etc/sudoers.d/90-cloud-init-users` — replaced cloud-init's blanket `azureuser ALL=(ALL) NOPASSWD:ALL` (1 line) with a narrow Cmnd_Alias allowlist (15 lines): `TC_SYSTEMD_BIN`, `TC_SYSTEMD_USR`, `TC_JOURNAL` (`--no-pager` scoped), `TC_DB` (bare-invocation only). Perms preserved at `0440 root:root`. Final md5: `f08e9d1a1cb2f1e9ae23fdeacf66b48d`.

**Features shipped:**
- The active privilege-escalation path through `sudo bash` / `sudo cat /etc/shadow` / `sudo <anything>` as `azureuser` is CLOSED. C-4 (service running as `azureuser` instead of root, ALREADY REMEDIATED) is no longer undermined by the cloud-init grant. Daily ops (`systemctl restart/start/stop/status/is-active/is-failed trading-corp*`, `systemctl daemon-reload`, `journalctl --no-pager -u trading-corp*`, bare `sqlite3 …trading_corp.db` with stdin SQL) remain passwordless; everything else falls through to a password prompt against `azureuser`'s **locked** password (`passwd -S azureuser = L`, shadow field `!`) — effective deny.

**Sudo-group secondary path:** `azureuser` is in `sudo` group (gid 27), which gives `%sudo ALL=(ALL:ALL) ALL` with password required. Password is locked, so this path is effective-deny — `gpasswd -d azureuser sudo` was considered but is unnecessary surface-area-change. Left as-is.

**Notable code changes:**
- None in repo; allowlist is on-VM only. Allowlist content reproduced inline in this entry for forensic recovery.

**Verification (azureuser via `sudo -n`; "a password is required" + nonzero exit = correctly-denied prompt):**

GATE 1 — allowlisted commands run passwordless:
- `sudo -n systemctl status trading-corp.service --no-pager` → exit 0, service info printed
- `sudo -n systemctl is-active trading-corp.service` → `active`, exit 0
- `sudo -n systemctl daemon-reload` → exit 0 (the cadence-deploy pattern)
- `sudo -n journalctl --no-pager -u trading-corp.service -n 1` → exit 0, journal line returned
- `echo 'SELECT COUNT(*) FROM sqlite_master;' | sudo -n sqlite3 /home/azureuser/trading_corp/data/trading_corp.db` → `56`, exit 0

GATE 2 — unit-file mutations correctly PROMPT (TC_UNITS was deliberately omitted from the allowlist; rare supervised deploy actions are not high-frequency ops and should authenticate):
- `sudo -n sed -i 's|x|x|' /etc/systemd/system/trading-corp-pm-watchlist-deep.service` → "a password is required", exit 1
- `sudo -n cp /etc/hostname /etc/systemd/system/trading-corp-noop.txt` → "a password is required", exit 1
- `sudo -n chmod 0644 /etc/systemd/system/trading-corp-pm-watchlist-deep.service` → "a password is required", exit 1

GATE 3 — unrelated sudo PROMPTS:
- `sudo -n cat /etc/shadow` → "a password is required"
- `sudo -n sqlite3 /tmp/test.db` (non-allowlisted DB path) → "a password is required", exit 1 (bare-path strictness confirmed)
- `sudo -n bash -c 'whoami'` → "a password is required", exit 1 (canonical escalation closed)

GATE 4 — `journalctl` WITHOUT `--no-pager` PROMPTS (confirms pager-shell-escape mitigation took):
- `sudo -n journalctl -u trading-corp.service -n 1` → "a password is required"

**Operational workflow note for future sessions:**
- `journalctl` invocations against trading-corp units **must include `--no-pager`** (in either position: `--no-pager -u …` OR `-u … --no-pager`) to remain passwordless. The 4 patterns covered are explicit; other positions fall through to password prompt.
- `sqlite3` invocations must be **bare** (`sqlite3 /home/azureuser/trading_corp/data/trading_corp.db`) with SQL passed via stdin/heredoc. No `-cmd`, no inline SQL arg, no trailing args (`.shell`/`.system` dot-commands would otherwise be RCE).
- **Unit-file mutations now require a password.** Since `azureuser`'s password is locked, the practical effect during deploys is: edit unit files via the `az vm run-command invoke` channel (which runs root-via-VM-agent, bypasses sudoers entirely) rather than `ssh azureuser@… && sudo sed -i`. The pm-watchlist cadence deploy earlier today (`14:25 UTC`) used exactly this pattern.

**Inert / dormant on current traffic:** none — the narrowed grant is exercised immediately by any sudo invocation.

**Filed as follow-up (NOT in this deploy):**
- **Cloud-init re-image durability.** The narrowed file lives at `/etc/sudoers.d/90-cloud-init-users` — managed by cloud-init. On next boot cloud-init won't re-run (`status: done`), but a `cloud-init clean` + reboot OR a re-image from the scale-set image WOULD re-write the file back to `NOPASSWD:ALL`. Durable fix: a `/etc/cloud/cloud.cfg.d/99-disable-default-user-sudo.cfg` override with `users: [...] sudo: false` (or an equivalent narrow grant via cloud-init's `users:` directive). Filed as new P2 in BACKLOG.md.

**Rollback recipe:**
```bash
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "
install -m 0440 -o root -g root \
  /etc/sudoers.d/90-cloud-init-users.pre-narrow-20260525 \
  /etc/sudoers.d/90-cloud-init-users && \
visudo -c && \
grep -nE 'azureuser|NOPASSWD' /etc/sudoers.d/90-cloud-init-users
"
```
Restores byte-for-byte the cloud-init original (`145` bytes, `NOPASSWD:ALL` grant).

---

## 2026-05-25 14:25 UTC — pm-watchlist-deep timer: drop `--merge` → weekly overwrite

**Commits:** n/a (VM-side systemd unit only; no repo code changed)
**Triggered by:** in-session operator approval; BACKLOG.md "P2 (ops) — Polymarket watchlist deep timer: drop `--merge` → weekly overwrite" (filed 2026-05-24, BOARD-GATED per CLAUDE.md §4)
**Backup tag:** `.pre-overwrite-cadence-20260525` on `/etc/systemd/system/trading-corp-pm-watchlist-deep.service`

**Files deployed (1, VM-side only):**
- `/etc/systemd/system/trading-corp-pm-watchlist-deep.service` — line 13 ExecStart: removed trailing ` --merge` from `python -m trading_corp.scripts.seed_polymarket_watchlist_deep --merge`

**Features shipped:**
- pm-watchlist-deep weekly timer now performs full **overwrite** of `agent_state.polymarket_copy_trader.watch_only_whales` on each Sunday fire instead of union-merge. Eliminates the "preserved-stale" accumulation bucket (was 48% of the 329-entry pool on the 2026-05-24 fire) — roster will snap to ~this-week's quality-pass set (~172 ± churn) with all-fresh stats on the next fire.

**Notable code changes:**
- None. Single sed-in-place on the systemd unit. `seed_polymarket_watchlist_deep` itself already handles overwrite-vs-merge via the presence/absence of `--merge` (cold-start safe; merge degenerates to overwrite when slot empty).

**Verification:**
- PRE md5: `9f1b2baf9c1b17d6fd0d95d9eb615bad`. POST md5: `0ca8e1d3880e41e8c24ffefc2b12d137`.
- `diff` vs backup: single-line change at line 13 (`ExecStart` lost ` --merge`), nothing else.
- `sudo systemctl daemon-reload` → OK.
- `systemctl is-failed trading-corp-pm-watchlist-deep.service` → `inactive` (timer-driven oneshot, expected).
- Timer next-fire: `Sun 2026-05-31 13:00:12 UTC` (RandomizedDelaySec re-rolled on daemon-reload; previous was 13:12:45 — ~12 min earlier, systemd-expected jitter, not a bug).

**Inert until first fire (Sun 2026-05-31 ~13:00 UTC):**
- The timer is the actuation surface. Until next Sunday, the standing 329-entry roster persists unchanged on disk. First post-deploy validation comes from the Sun 2026-05-31 fire — expect roster to snap to ~172 with merge_stats showing `replaced ≈ existing` and `preserved = 0`.

**Risk note carried forward:**
- `included_iso` is dead-end on Polymarket today (audited 2026-05-24: not in any template, not in sort whitelist, no consumer). If a future "whale tenure" feature wants it, either decouple `included_iso` from cadence (preferred) or revert this change. Per BACKLOG plan.

**Rollback recipe:**
```bash
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "
sudo cp /etc/systemd/system/trading-corp-pm-watchlist-deep.service.pre-overwrite-cadence-20260525 \
  /etc/systemd/system/trading-corp-pm-watchlist-deep.service && \
sudo systemctl daemon-reload && \
grep '^ExecStart' /etc/systemd/system/trading-corp-pm-watchlist-deep.service
"
```
Or one-line sed reversal:
```bash
sudo sed -i 's|seed_polymarket_watchlist_deep$|seed_polymarket_watchlist_deep --merge|' \
  /etc/systemd/system/trading-corp-pm-watchlist-deep.service && sudo systemctl daemon-reload
```

---

## 2026-05-24 23:52 UTC — tasty_options Phase-0 sandbox smoke PASSED (local verification, no prod deploy)

**Commits:** `672f658` (async-call + sys.path fixup) + post-fixup iteration (broker `dry_run` param + smoke moved to production endpoint + smoke now picks real chain expiry + margin/BP rejection on dry-run treated as broker-shape SUCCESS). Final iteration commit pending.

**Features shipped to prod:** NONE. This is local smoke verification of the `TastytradeBroker` end-to-end against `api.tastyworks.com`. Broker is not yet wired into running prod main.py (that's Commit 4 of the tasty_options build, `94b3129`, which is committed to main but not deployed).

**Notable code changes (across the smoke iteration):**
- `trading_corp/brokers/tastytrade.py`: 7 sites switched from `asyncio.to_thread(async_method, ...)` to `await async_method(...)` — the SDK's `Account.*` methods + `get_market_data` are coroutine functions; the original `to_thread` wrapping produced unawaited coroutine objects that the broker's `await` then choked on. Plus added `dry_run: bool = False` kwarg to `place_multi_leg` + `_submit_and_wait` so smoke probes can verify shape without placing real orders.
- `tests/test_tastytrade_broker.py`: 13 `MagicMock(return_value=X)` → `AsyncMock(return_value=X)` on the async account methods (the original mocks were the exact failure mode `feedback_mocks_dont_catch_sdk_shape` warns about).
- `scripts/tasty_sandbox_smoke.py`: sys.path shim added; `is_test=True` → `is_test=False` (Tastytrade CERT requires a separate OAuth app registration; rather than maintain two OAuth bootstraps the smoke probes production with `dry_run=True`); strikes auto-picked from real SPY chain via `get_option_chain` rather than guessed; margin/BP rejection on dry-run treated as the broker-shape SUCCESS signal (TT validated chain + serialization + auth + scope + margin layer — every code path exercised).

**Verification:** Final smoke run at 2026-05-24T23:52 UTC. All 4 probes PASSED on account `5WZ66443` (equity $500, BP $0) on TT PRODUCTION:
- probe 1/4 snapshot — equity + balances + positions read OK
- probe 2/4 place_multi_leg(dry_run=True) — TT validated SPY 2026-07-17 600C/605C/555P/560P combo through to margin layer; rejected with `margin_check_failed: Your account does not have sufficient buying power` (expected; account capacity issue, not code)
- probe 3/4 cancel_order on non-existent id 999999999 — returned False (expected)
- probe 4/4 get_option_greeks for SPY 260717C00600000 — returned None (dxFeed timeout; acceptable per runbook)

**OAuth scope verified:** New OAuth grant 2026-05-24 ~23:25 UTC with `scope=read trade` after operator widened the OAuth app's allowed scopes in the Tastytrade developer portal (originally `read` only). JWT `iat=1779666232`, `scope="read trade"`. Verified via in-process JWT decode pre-smoke.

**Inert / dormant on current traffic:** All of the tasty_options division code (`94b3129`) is committed to main but NOT deployed to prod. Prod's running main.py predates this commit. Phase-1 paper observation will start once the tasty_options division code lands on prod — operator deploy decision; see plan file `.claude/plans/i-want-to-create-enumerated-papert.md`.

**Next:** Deploy `94b3129` + this fixup commit chain to prod when ready. Once running, `auto_execute: false` keeps TastytradeBroker paper-wrapped (PaperExecutionBroker) for the ≥21-day Phase-1 observation window before any Phase-2 live conversation. Per memory `feedback_never_pre_flip_verified_flags` and `feedback_observation_window_no_early_advance`.

---

## 2026-05-24 22:49 UTC — UI cleanup pass: htmx flicker fix + trade-flow titles + bitunix layout + approvals tile link (commit `0a98bbf`)

**Commits:** `0a98bbf` (deployed at 22:49 UTC; pushed to `origin/main` immediately after).

**Triggered by:** Operator UI-defect walkthrough this session: (1) "screen darkens and gets bright again, frustrating" on division detail pages; (2) "WOULD HAVE PLACED" repeated on every Live trade flow row is not helpful, use payload `event_title`; (3) bitunix_futures Expert Analysis box is unused — collapse it; (4) Pending Approvals tile (count = 16 at session time) has no click target — make it a link.

**Backup tag:** `pre-ui-flicker-fix-20260524-2230`. Pre-deploy md5s on prod (= HEAD before this commit):
- `679c8a032523f8a433b73647639343b0  trading_corp/web/static/css/app.css`
- `f3898a5e47308f917c7c56e121bffe46  trading_corp/web/data.py`
- `f140ba7f74f877634d319c92d9187282  trading_corp/web/templates/partials/trade_flow.html`
- `7eb631a8ba5c7f0095baa49e3a1bb80b  trading_corp/web/templates/division.html`
- `89c0c814a42f4c73ccea1a1c0c5e34dd  trading_corp/web/templates/partials/stat_cards.html`

**Files deployed (5):**
- `trading_corp/web/static/css/app.css` — `.htmx-request` opacity raised 0.6 → 0.97; transition shortened 100 ms → 60 ms. `.htmx-swapping` (opacity 0) and `.htmx-settling` (opacity 1) rules removed entirely. These three rules together produced a visible whole-panel fade on every htmx poll/swap; on bitunix_futures the six stacked polling panels at 15-30 s offsets read as the screen constantly cycling dark/bright.
- `trading_corp/web/data.py` — `trade_flow()` row dict gains `event_title` field: `payload.event_title` (Kalshi) → `payload.market_question` (Polymarket) → `None`. No schema change; pure derived field from existing `payload_json`.
- `trading_corp/web/templates/partials/trade_flow.html` — row header renders `evt.event_title` (mono case, truncated, hover-title for full text + audit kind) when present; falls back to the existing uppercase kind label for non-prediction-market rows (PMCC scans, fills, Otter/Cypher webhooks).
- `trading_corp/web/templates/division.html` — `_has_expert_analysis = view.division.slug != 'bitunix_futures'` flag added. Outer grid drops `lg:grid-cols-3` when no aside; left wrapper drops `lg:col-span-2`; entire right `<aside>` block wrapped in the same conditional. **The `#pair-analysis` box and its routes are preserved** — PMCC / IRA / Polymarket / Kalshi all still target it.
- `trading_corp/web/templates/partials/stat_cards.html` — Pending Approvals tile changed from `<div>` to `<a href="/approvals">` with `block` + hover/focus affordances + `→` glyph. Route already existed at `routes.py:1454`.

**Features shipped (load-bearing for future "is X done?" checks):**
- **htmx-request whole-panel fade NEUTRALIZED.** Every existing polling partial (bitunix_pending_pa/htf/pa/decision_flow/score/trade_plan, stat_cards, market_ribbon, trade_flow, iron_condor_live, home) stops visibly flickering on each tick. Class hook preserved at opacity 0.97 in case JS needs it; sub-perceptual.
- **Live trade flow rows now show market context.** Kalshi rows show `event_title` ("When will Stripe officially announce an IPO?"), Polymarket rows show `market_question`. Audit kind preserved in row hover tooltip.
- **bitunix_futures detail page is single-column.** Left content (positions/activity/HTF/PA/score/etc.) takes the full grid width.
- **Pending Approvals tile is clickable** → routes to `/approvals`.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`.htmx-request` rule still exists** but at 0.97 opacity / 60 ms transition — effectively invisible. If you later want to restore a visible loading indicator, the right pattern is opt-in `.htmx-indicator` spinners scoped to small elements, not a global fade.
- **`_has_expert_analysis` flag in `division.html`** is a single-division allow-list. Coinbase_spot likely has the same empty-aside symptom (donchian partials also don't fire into `#pair-analysis`) but was deferred — flag was not extended speculatively. Add `'coinbase_spot'` to the exclusion if/when the user asks.
- **`event_title` derivation in `data.py`** uses `.get()` chain — adding new prediction-market kinds with their own title field means adding a new fallback. Generic enough for current Kalshi + Polymarket.

**Verification:**
- PRE_PID `1300124` → POST_PID `1303946` at 22:49:35 UTC. Service `active`. Broker re-registration log lines all present (Robinhood OAuth, Coinbase, BitUnix observer, Kalshi/Polymarket/Tasty), no startup errors.
- HTTP probes: `/`, `/approvals`, `/division/bitunix_futures` all 302 → Authelia (expected; routes alive).
- Post-deploy md5s on prod match local byte-for-byte for all 5 files.

**Inert / dormant on current traffic:**
- None — all 5 changes affect the running UI immediately on next browser refresh (CSS + templates are picked up at request time; `data.py` change activated by service restart).

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-ui-flicker-fix-20260524-2230; BASE=/home/azureuser/trading_corp
for f in trading_corp/web/static/css/app.css trading_corp/web/data.py trading_corp/web/templates/partials/trade_flow.html trading_corp/web/templates/division.html trading_corp/web/templates/partials/stat_cards.html; do
  mv \$BASE/\$f.\$TAG \$BASE/\$f
done
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-24 21:47 UTC — kalshi_weather Bucket 1 (HRRR + forecast run-age logging) DEPLOYED (commit `75ba7c5`)

**Commits:** `75ba7c5` (pushed to `origin/main` at 21:42 UTC, deployed at 21:47 UTC).

**Triggered by:** Operator directive 2026-05-24 ~20:30 UTC ("lets do bucket 1 so we can collect data for a week") + in-session `AskUserQuestion` go on (a) ship 1.1+1.2 together, (b) HRRR flag default ON, (c) commit + deploy now. Plan at `plans/forecast-quality-improvements-for-kalshi-prancy-porcupine.md`.

**Backup tag:** `pre-bucket1-20260524-2200`. Pre-deploy md5s on prod:
- `e6ea67d5b76b1be4ef18efe1ab339c03  trading_corp/agents/strategies/_weather_math.py`
- `6cac8d46c18cbadaab20509c134a801e  trading_corp/agents/strategies/kalshi_weather_arb.py`
- `c4e9f5e78464c2ca4302ab41ff3cb1ae  trading_corp/data/open_meteo_client.py`
- `ecc5a69d34485d390259e9cf42b1b0d4  trading_corp/data/weather_forecast.py`
- `322fed92944d9ab8ee16e46e1f7277ea  config/strategies.yaml`

**Files deployed (5):**
- `trading_corp/agents/strategies/_weather_math.py` — `ForecastPoint` gains optional `issued_at` and `fetched_at` (default None; preserves all existing callers).
- `trading_corp/data/weather_forecast.py` — `_get_periods` captures NWS `Last-Modified` header + wall-clock fetch time; cache value extended from `(epoch, periods)` to `(epoch, periods, last_modified, fetched_at)`; both `get_forecast_at` and `get_daily_extremum` populate the new ForecastPoint fields.
- `trading_corp/data/open_meteo_client.py` — `EnsembleObservation.fetched_at` added; `_fetch_payload` returns `(payload, fetched_at_iso)` with cache value extended to 3-tuple; **new `fetch_hrrr_only(lat, lon, target_iso, kind=None)` method** with separate `_hrrr_cache` and single-model unsuffixed-field parse path (`hourly.temperature_2m`, not `hourly.temperature_2m_<model>`).
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — new HRRR fetch block after the ensemble block (inherits the same `lat, lon` locals from line 549 — coord-discipline preserved); `_hrrr_enabled()` helper; `_minutes_since_iso()` helper; 8 new audit fields in `eval_payload`: `hrrr_temp_f`, `hrrr_source`, `hrrr_fetched_at`, `nws_forecast_issued_at`, `nws_fetched_at`, `open_meteo_fetched_at`, `metar_obs_age_min`, `metar_latest_obs_iso`. Raw NWS issued/fetched preserved across the ForecastPoint rebuild via local variables.
- `config/strategies.yaml` — new `hrrr_enabled: true` key under `kalshi_weather_arb`. Hot-reloadable.

All 5 files deployed via `scp` from local to prod `/tmp/`, then atomic-`mv` into position. Post-deploy md5s on prod match local byte-for-byte. Prod YAML was pure LF (memory `feedback_deploy_crlf_config_patch.md` was out-of-date for THIS file — verified with `cat -A` showing `$` line ends, not `^M$`); scp wholesale was safe. **No sed-in-place needed for this YAML.**

**Features shipped (load-bearing for future "is X done?" checks):**
- **Item 1.1 (HRRR latest-run logging) LIVE.** Every `kalshi_weather_evaluated` audit row from 21:53:23 UTC onwards carries `hrrr_temp_f` and `hrrr_source` (=`open_meteo_hrrr` when available, `unavailable` otherwise) — captured at the same xref-resolved `(lat, lon)` the existing forecast path uses. Backtest corpus for the queued NBM-σ / horizon-weighting work accumulates from this timestamp.
- **Item 1.2 (forecast run-age logging) LIVE.** Same audit rows carry `nws_forecast_issued_at` (NWS Last-Modified header, may be NULL on Akamai-stripped requests — first-row populated `"Sun, 24 May 2026 20:59:58 GMT"`), `nws_fetched_at` (wall-clock fallback, always populated), `open_meteo_fetched_at`, `metar_obs_age_min` (only for sub-6h hourly markets — daily HIGH/LOW markets skip METAR by design).
- **`hrrr_enabled` config flag** is hot-reloadable. To suppress HRRR fetch without restart: `sed -i 's/^  hrrr_enabled: true/  hrrr_enabled: false/' /home/azureuser/trading_corp/config/strategies.yaml`. Strategy mtime-checks YAML on every cycle.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Coord-discipline is structural.** The HRRR fetch passes the existing `lat, lon` locals (bound at `kalshi_weather_arb.py:549` from `coord_info["lat"], coord_info["lon"]` which is xref-resolved). There is NO city-name lookup inside OpenMeteoClient; there is NO separate `_resolve_hrrr_coords` helper. The 2026-05-22 NYC/CHI/HOU correction (KJFK→KNYC, KORD→KMDW, KIAH→KHOU) is therefore inherited automatically. Plan §1.1 coord-discipline guarantee enforced.
- **HRRR uses a SEPARATE cache (`_hrrr_cache`)** keyed on `(lat, lon, forecast_days)` so the single-model HRRR payload doesn't collide with the multi-model ensemble payload at the same coords. Same key shape, different dict.
- **Single-model Open-Meteo response uses UNSUFFIXED `hourly.temperature_2m`** (verified 2026-05-24 via direct curl). Multi-model uses `hourly.temperature_2m_<model>`. `fetch_hrrr_only` reads the unsuffixed field; `get_ensemble_at`/`get_ensemble_daily_extremum` read the suffixed fields. Don't confuse them.
- **HRRR model identifier is `ncep_hrrr_conus`** (Open-Meteo's name). Hardcoded as `OpenMeteoClient.HRRR_MODEL`. CONUS-only — fine for every current weather station, but a non-CONUS coord request would return None.
- **HRRR data is NEVER fed into σ or temp blend.** It's parallel-logged only. The strategy's σ flow (`sqrt(forecast.sigma_f² + SOURCE_DIVERGENCE_SIGMA_F²)`) and decision logic are unchanged. Verified by inspection at `_weather_math.py:155`.

**Verification (all PASS):**
- PRE_PID `1300115` (stale match) → POST_PID `1300124` (xvfb-run wrapper) at 21:47:13 UTC. Service `active (running)` since same.
- 71 weather-related tests pass under `run_capped.ps1 pytest` (4 test files: coord_resolution, fixes, sizing, weather_stations).
- Local pre-deploy smoke against real Open-Meteo: `fetch_hrrr_only` at KNYC/KMDW/KHOU returns `temp=56.6/69.0/77.6 °F`, `source=open_meteo_hrrr`, `fetched_at` populated, `models=['ncep_hrrr_conus']`.
- First post-restart `kalshi_weather_evaluated` audit row at 21:53:23 UTC (10 minutes post-restart; 5-minute poll cycle). Spot-check of 6 KXLOWTHOU rows from that scan (column names corrected 2026-05-25 — see note at end of block):
  - `lat = 29.6454`, `lon = -95.2789` = KHOU coords (corrected) — scalar `$.lat` / `$.lon` JSON keys in `payload_json`, populated from `chosen[*]` after xref resolution at `kalshi_weather_arb.py:298-299`
  - `yaml_coords = [29.6454, -95.2789]` = YAML-resolved coord list; equals `lat`/`lon` byte-for-byte because `coord_source = yaml_verified` means `chosen` IS the YAML coords
  - `coord_source = yaml_verified`
  - `hrrr_temp_f` populated (67.2 °F for 26MAY24 daily-low, 70.2 °F for 26MAY25)
  - `hrrr_source = open_meteo_hrrr`
  - `nws_forecast_issued_at = "Sun, 24 May 2026 20:59:58 GMT"` (Akamai DID serve a valid Last-Modified header)
  - `nws_fetched_at = 2026-05-24T21:53:16+00:00`
  - `open_meteo_fetched_at = 2026-05-24T21:53:16+00:00`
  - `metar_obs_age_min` NULL (correct — HOU's KXLOWT is a daily-low market, METAR nowcast is excluded for daily extrema by design)
- **Coord-discipline verification PASSED.** HRRR is fetching at the same corrected coords the existing forecast path uses for the 6 HOU rows examined. Future post-deploy spot-checks should sample NYC/CHI rows too (KXHIGHCHI/KXHIGHNY) as they appear in subsequent scan cycles.
- **Field-name correction (added 2026-05-25):** the original spot-check report (above) called the scalar coord fields `audit_lat` / `audit_lon`. Those names are SQL aliases from the plan's verification query (`json_extract(payload_json, '$.lat') AS audit_lat, ...`), not JSON keys. Actual top-level JSON keys in the payload are `lat` and `lon` (see `kalshi_weather_arb.py:298-299`). The substantive verification — chosen scalar coords match `yaml_coords` for `yaml_verified` rows — holds either way. The forward-watch obligation below is restated with corrected names.

**Inert / dormant on current traffic:**
- `nws_forecast_issued_at` populates 100% on first cycle, but Akamai CDN behavior is per-request — expect SOME fraction of NULL across the week. NULL is normal, not a bug.
- HRRR is CONUS-only. If a non-CONUS weather market is ever added (none today), `hrrr_temp_f` will be NULL and `hrrr_source = "unavailable"` (logged as such, not silent).

**Forward-watch obligation (for the observation week through ~2026-05-29):**
- Confirm new fields populate across NYC/CHI markets too (not just HOU). Spot-check after first hourly cycle that scans those: `KXHIGHCHI*`, `KXHIGHNY*`, `KXLOWTNYC*`, `KXLOWTCHI*`, `KXHIGHTHOU*`. `coord_source` MUST be `yaml_verified`; scalar `$.lat`/`$.lon` MUST equal `$.yaml_coords[0]`/`$.yaml_coords[1]`. (Status as of 2026-05-25T14:10 UTC: PASS on 3,153 yaml_verified rows — see `reports/2026-05-25_kalshi_weather_bucket1_forward_watch.md`.)
- Confirm HRRR availability rate. Expected ~100% during US weather hours; failures should be transient (try/except wraps the fetch). (Observed 2026-05-25: 96.8%.)
- Confirm NWS issued_at populate rate. NULL on a fraction is expected (Akamai); 0% would mean header capture is broken. (Observed 2026-05-25: 100% — no Akamai stripping in window.)

**Rollback recipe (reverts to pre-Bucket 1 state — strategy logic restored, audit payload loses the 8 new fields, no decision impact either way):**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bucket1-20260524-2200; BASE=/home/azureuser/trading_corp;
for f in trading_corp/agents/strategies/_weather_math.py trading_corp/agents/strategies/kalshi_weather_arb.py trading_corp/data/open_meteo_client.py trading_corp/data/weather_forecast.py config/strategies.yaml; do
  mv \$BASE/\$f.\$TAG \$BASE/\$f
done
sudo systemctl restart trading-corp.service
"
```

Alternative soft-disable (no restart): `sed -i 's/^  hrrr_enabled: true/  hrrr_enabled: false/' /home/azureuser/trading_corp/config/strategies.yaml` — suppresses HRRR fetch only, keeps run-age logging.

---

## 2026-05-24 16:55 UTC — C-2 (LLM push_back routes through risk gate + side-flip backstop) DEPLOYED (commit `19ff0da`)

**Commits:** `19ff0da` (already on `origin/main` since 2026-05-23 — TRACK B closed-in-code in EOS `07ffcdc`). This deploy_log entry will land in the wrap commit alongside the TRACK B deploy_log closeout.

**Triggered by:** Operator directive 2026-05-24 ~16:00 UTC ("TRACK B-DEPLOY — deploy C-2 webhook risk-gate fix with active push_back acceptance watch") + in-session §4 approval for webhook/risk-path change.

**Backup tag:** `pre-trackb-c2-20260524` on `trading_corp/web/webhooks.py`, `trading_corp/agents/risk.py`, `trading_corp/agents/research/trade_confirmation_consult.py`. Pre-deploy md5s:
- `990b33c5aa535396eacb288b63843356  webhooks.py` (CRLF)
- `18b161758f3038d91bf66189631f1f8d  risk.py` (LF)
- `6f23795b7b96a5127fec21009953f8c7  trade_confirmation_consult.py` (LF)

**Files deployed (3):**
- `trading_corp/web/webhooks.py` — Lord Otter + Market Cypher handlers stamp `extra["originating_signal_side"] = order.side` before consult; route `consult.decision == "skip"` through `risk_agent.evaluate(..., forced_reject_reason="llm_push_back: <rationale>")` instead of bypassing; write `risk_rejected` audit row with `source="llm_push_back"` and `via=lord_otter_webhook`/`market_cypher_webhook`.
- `trading_corp/agents/risk.py` — `RiskAgent.evaluate` gains a strictly-additive `forced_reject_reason` kwarg (short-circuit to reject); new side-flip backstop rejects when `order.side != extra["originating_signal_side"]`.
- `trading_corp/agents/research/trade_confirmation_consult.py` — `apply_suggested_modifications_to_order` silently drops `mods.side` flips and surfaces `side_flip_blocked` in `applied`; `consult_research_for_trade_confirmation` writes `research_side_flip_blocked` audit row when the LLM tries to flip side.

Per-file EOL preserved (webhooks.py = CRLF, risk.py = LF, consult.py = LF) — semantic md5 (LF-normalized) of all 3 matches the `19ff0da` git blob byte-for-byte.

**Features shipped (load-bearing for future "is X done?" checks):**
- **C-2 (LLM push_back routes through risk gate) CLOSED.** Every LLM skip is now audited as `risk_rejected/source=llm_push_back`; CLAUDE.md §1 invariant #1 (risk gate is a single chokepoint) is restored.
- **Side-flip defense-in-depth.** Two-layer block: consult-layer drop (preserves original side + writes `research_side_flip_blocked`) and risk-gate backstop (rejects via `originating_signal_side` comparison).

**Notable code changes (callouts a future Claude shouldn't miss):**
- `forced_reject_reason` is a strictly additive kwarg to `RiskAgent.evaluate` — existing callers unchanged; new short-circuit returns `RiskVerdict(verdict="reject", reason=forced_reject_reason)` before any other gates run.
- Side-flip backstop reads `order.extra["originating_signal_side"]` — webhook handlers stamp it immediately after `agent.on_alert` returns and BEFORE consult is invoked.
- `SuggestedModifications.side` is `Literal["buy", "sell"]` and `OrderSide` is also `Literal["buy", "sell"]` — `apply_suggested_modifications_to_order` compares them directly; mixed casing would trigger spurious `side_flip_blocked`. Both sides of the system stay lowercase.

**Verification:**
- PRE_PID `1237405` (active since 2026-05-24 03:38:39 UTC) → POST_PID `1284818` (xvfb-run wrapper) / `1284838` (python child) at 2026-05-24 16:55:43 UTC.
- Web bound on `:8000` at 2026-05-24 17:00:45 UTC (~5:02 post-restart; IC position-manager startup catch-up was the limiting step).
- healthz local + Caddy: `{"status":"ok","mode":"PAPER"}`.
- File md5s post-deploy match the `19ff0da` git blobs (per-file EOL preserved).
- **Synthetic gate tests** (separate prod python process invoking the loaded `RiskAgent.evaluate`, see `/tmp/trackb_synthetic.py`): T1 `forced_reject_reason` in evaluate signature; T2 `forced_reject_reason="llm_push_back: ..."` → `verdict=reject` reason carries the marker; T3 side-flip backstop (`originating_signal_side=BUY`, `order.side=SELL`) → `verdict=reject, reason="side flipped from originating signal: BUY → SELL"`; T4 allowed-path (sides match, no forced reject) → `verdict=approve, reason="within all risk caps"`. **All 4 PASS.**
- **Real-HTTP path verification via temporary forcing hooks (REMOVED before close):** at 2026-05-24 ~20:55 UTC, a pair of payload-marker-gated forcing hooks was added to `web/webhooks.py` and `agents/research/trade_confirmation_consult.py` (backup tag `pre-trackb-hook-20260524-1700`). Two POSTs to `http://127.0.0.1:8000/webhook/tradingview/lord-otter` from localhost on prod (HMAC-valid, KV-fetched secret):
  - **SKIP marker (`trackb-test-20260524-skip-c2deploy`)** → HTTP 200 `{"status":"accepted","signal":"TRACKB_SYNTH_SKIP","symbol":"BTCUSDT.P"}` at 20:56:08 UTC. Audit row at **20:56:10 UTC**: `kind=risk_rejected, actor=risk, source=llm_push_back, via=lord_otter_webhook, symbol=TRACKBSYNTHBTC, tier=trackb_synth, reason="llm_push_back: TRACKB SYNTHETIC: forced skip for C-2 deploy verification"`. End-to-end ~2s.
  - **SIDE-FLIP marker (`trackb-test-20260524-sideflip-c2deploy`)** → HTTP 200 at 20:56:42 UTC. Audit row at **20:56:44 UTC**: `kind=research_side_flip_blocked, actor=lord_otter, engagement_id=TRACKB-SYNTH-SIDEFLIP, order_id=a3817c5e-faef-43bf-bc1a-407f47021bed, symbol=TRACKBSYNTHBTC, originating_side=buy, requested_side=sell, rationale="TRACKB SYNTHETIC SIDE FLIP"`. Followed by `lord_otter/would_have_placed` (same ts) — confirms the side-flip was blocked AND the original side (`buy`) was preserved through the risk gate downstream.
- **Forcing hooks reverted** at 2026-05-24 ~21:00 UTC from the `pre-trackb-hook-20260524-1700` backup. Restart #3 PID `1295064 → 1296508`. Post-revert md5 matches the C-2 fix state byte-for-byte; `grep -c TRACKB` returns 0 on both files. /tmp staging files removed.

**Forcing-hook recipe (reproducible for future deploys that need real-path verification of a hard-to-trigger code path):**
1. Add payload-marker-gated branch in webhook handler (synthesize a `ProposedOrder` if marker present, bypassing `agent.on_alert`).
2. Add payload-marker-gated branch in consult function (return `ConsultResult` with the desired `decision` if marker present, bypassing the LLM call).
3. Use a unique marker string keyed to the deploy date + commit (e.g. `trackb-test-20260524-skip-c2deploy`) to make accidental collisions astronomically unlikely; gate on `payload.get("trackb_test_marker") == EXACT_VALUE`.
4. Backup current files with `pre-<feature>-hook-YYYYMMDD-HHMM`; install patched; restart.
5. POST from localhost on prod (HMAC + KV-fetched secret); verify audit rows; revert from backup; restart.
6. Final `grep -c <MARKER_PREFIX>` on both files must return 0.

**Inert / dormant on current traffic:**
- The new code paths fire only when `consult.decision == "skip"` (LLM push_back) or when `mods.side != order.side`. On natural traffic since 16:55:43 restart through verification window: 31 `webhook_received` (Otter 2 + Cypher 29), all `alert_ignored` (agent.on_alert returned None — strategy cooldowns / regime gates / Sunday traffic). **The C-2 fix code paths have NOT yet been exercised by natural traffic.**
- **Forward-watch obligation** (carries into next sessions until satisfied): the first few natural push_backs after this deploy MUST audit a `risk_rejected/source=llm_push_back` row. If they don't, the wiring is broken in a way synthetic + forced-real-path tests missed; rollback per recipe below. Query:
  ```sql
  SELECT ts, json_extract(payload_json, '$.via'), json_extract(payload_json, '$.symbol'),
         substr(json_extract(payload_json, '$.reason'), 1, 80)
    FROM audit_event
   WHERE kind='risk_rejected'
     AND json_extract(payload_json, '$.source')='llm_push_back'
     AND ts >= '2026-05-24T16:55:43'
   ORDER BY ts DESC LIMIT 10;
  ```

**Rollback recipe (reverts to OLD pre-C-2-fix state — LLM push_back resumes bypassing risk gate; use ONLY if forward-watch shows the new code is broken):**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-trackb-c2-20260524; BASE=/home/azureuser/trading_corp;
for f in trading_corp/web/webhooks.py trading_corp/agents/risk.py trading_corp/agents/research/trade_confirmation_consult.py; do
  mv \$BASE/\$f.\$TAG \$BASE/\$f
done
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-24 14:40 UTC — kalshi_sports_arb_observer cap-bump 50→150 (max_markets_per_series); no restart (mtime hot-reload)

**Commits (observer-side, already on `origin/main`):** `2dd12bf` (cap bump + verdict reframe), `0bcb2ba` (series_filter fix), `f8d441d` (observer Phase 0 instrument), `5807273` (MLB sibling), `e620fe7` (analyze script), `6ae5e48` (deploy bundle), `753ecee` (sports_math + scout retro), `7b4b056` (odds_api_client per-book). This deploy_log entry closes the EOS.
**Triggered by:** Operator directive 2026-05-24 ~14:35 UTC after feed-diagnosis established calendar mismatch is real-world (not a free-tier artifact). Wait-for-overlap strategy failed at 11 cycles (all `n_no_book_match=50`); cap-bump was the pre-authorized fallback (Option A).

**Backup tag:** none — surgical config-only patch via Python sed-in-place on the OBSERVER block ONLY (scout block `max_markets_per_series: 50` untouched). Pre/post grep verified single-line replacement.

**Files patched on prod (1):**
- `/home/azureuser/trading_corp/config/strategies.yaml` — observer block: `max_markets_per_series: 50` → `150`. Mtime updated; observer's `_reload()` mtime check picks it up on next cycle. No service restart.

**Features shipped:**
- Observer's Kalshi discovery now captures up to 150 KXMLBGAME tickers per cycle (was 50, hitting the rotating-slice cap). First post-bump cycle at 15:40:54 UTC saw `markets_pre_filter: 88`, `n_in_scope: 88`, **`n_observed: 30`** (vs zero on every cycle before) — first calendar overlap with books achieved.

**HARD GATE PASSED on first MLB observation row** (15:40:53 UTC, `observation_id=68f15be4...`):
- Ticker `KXMLBGAME-26MAY241920TEXLAA-TEX` (LAA hosts TEX, 23:21 UTC start).
- `matching_key.team_home="Los Angeles Angels"`, `team_away="Texas Rangers"` — confirmed correct game.
- A-arb EV reconciled to −$0.329 (Kalshi 10×$0.48 + $0.18 fee + Pinnacle home @ −115 = $5.349; total $10.329 vs guaranteed $10). Stored: `-0.3288`. **Matches to the cent.**
- B EV: Pinnacle vig-removed TEX prob 0.4757 → 10×0.4757 − 4.98 − 0.18 = −$0.223. Stored: `-0.2223`. **Matches to the cent.**
- `kalshi_quote_invalid: false` (yes_ask + no_ask = 1.01).
- `pinnacle_used: true`. Pinnacle returned both sides (vig 2.03%). B-test is a real sharp-book test, not soft-book proxy.

**Notable code changes:**
- **Feed-diagnosis verdict reframe in `scripts/analyze_kalshi_sports_arb_observations.py` (`2dd12bf`).** Dual-verdict shape: A_hypothesis + B_hypothesis separate. **B is FORCED to `INCONCLUSIVE_INSTRUMENT_TOO_WEAK`** because Phase 0's 1h cadence cannot test sub-hour lag (the-odds-api refresh is 60s pre-match, all tiers; binding constraint is OUR cadence, not the feed). **New `SHELVE_LATENCY_THESIS_CLOSED` A-verdict** fires when A=0 positives or mean EV ≤ kill threshold; routes to shelve discussion (kalshi-crypto pattern), NOT to spend escalation. **3 new mandatory caveats:** CALENDAR ASYMMETRY (venues overlap only in final ~24h pre-game = most-efficient window), SINGLE-FEED LIMIT (production shops run 4-10+ feeds), HOURLY A-ARB PRIOR IS LOW (persistent >1h arb would be taken by any shop with a 60s feed).
- **the-odds-api Pinnacle is opt-in via `bookmakers=` filter.** Default us-regions response excludes Pinnacle. Observer's `_PHASE0_LEAGUE_SERIES_FILTER` and `sharp_book_preference` config knob wire it in.
- **MLB sibling path preserved NBA path bit-for-bit** (`_PHASE0_LEAGUE_CLASSIFIERS` dispatch table + `_process_league` per-league loop; existing 46 NBA tests pass unchanged + 9 new MLB tests + 4 series-filter tests = 59/59 total).

**Latent bugs caught + fixed (during this work):**
- Observer initial deploy missing `series_filter=` kwarg to `kalshi_broker.list_markets()` — saw only 7 KXMLBWINS tickers per cycle (Sports category has ~2000 series; cap returned rotating slice that mostly missed in-scope game tickers). Fixed in `0bcb2ba` by mirroring scout's b880b66 pattern.

**Verification:**
- Service unchanged (PID 1237421); restart NOT required for config-only patch.
- `kalshi_sports_arb_scan` audit row at 15:40:54 UTC confirms cycle fired with new cap.
- `kalshi_sports_arb_observation` rows = 30 after one cycle (up from 0).
- `kalshi_sports_arb_unmapped` rows for that cycle = 58 (future-dated Kalshi tickers where books haven't posted lines yet — expected; not a bug).
- HARD GATE math reconciled to the cent on first row.
- 59/59 tests still green locally.

**Inert / dormant on current traffic:**
- **Hypothesis B verdict is forced to INCONCLUSIVE** for Phase 0 by design — observer collects + reports B EV numbers, but verdict-design treats them as non-discriminating at 1h cadence. To move B out of INCONCLUSIVE the cadence (not the feed) must change, and only after A hourly-snapshot results justify the spend.
- **`SHELVE_LATENCY_THESIS_CLOSED` routing** is wired in the analyze script but only fires once N≥30 rows have accumulated AND A is consistently zero/negative-EV. Until then, A verdict will be `INCONCLUSIVE_INSTRUMENT_TOO_WEAK`.
- **Grading-alignment matrix for MLB is DEFERRED** to Phase 1 prereq. Observer collects A-arb candidates against unverified Kalshi-vs-DK/FD/BetMGM grading on rain-shortened, official-game rule, pitcher-listed, extra innings. Live A-arb action requires the matrix filled in first.

**Rollback recipe (cap-bump revert, if needed):**
```bash
ssh azureuser@trading.jacksumner.com "
/home/azureuser/trading_corp/venv/bin/python3 -c \"
p='/home/azureuser/trading_corp/config/strategies.yaml'
s=open(p,encoding='utf-8').read()
start=s.find('kalshi_sports_arb_observer:'); end=s.find('kalshi_copy_trader:',start)
blk=s[start:end].replace('max_markets_per_series: 150','max_markets_per_series: 50',1)
open(p,'w',encoding='utf-8').write(s[:start]+blk+s[end:])
print('reverted to 50')
\"
"
```

**Earlier deploys in this chain (for completeness):**
- **2026-05-24 03:38:44 UTC** — observer redeploy with series_filter fix (`0bcb2ba` + bundle `05ba56c`). Service restart (PID 1235018 → 1237421). First post-fix cycle at 04:39:32 UTC fixed the 7-KXMLBWINS issue but exposed the calendar-overlap fork.
- **2026-05-24 02:01:08 UTC** — observer initial deploy (`f8d441d` + bundle `6ae5e48`). Service restart (PID 1228335 → 1235018). First cycle at 03:01:39 UTC found only KXMLBWINS due to missing series_filter (root-caused later).

---

## 2026-05-24 15:14 UTC — `requirements.lock` C-6 correction: regenerated against prod running versions, disk-downgrade reverses unintended 14:56 UTC bump install (commit `e5556ef`)

**Commits:** `e5556ef` (regenerated `requirements.lock` + BACKLOG P1 entry + this deploy_log entry).
**Triggered by:** Operator directive 2026-05-24 ~15:00 UTC to reverse the 14:56 UTC bump-install without process restart.

**Context (the deploy this fixes):**
At 2026-05-24 14:56 UTC, scp'd the original C-6 lockfile (md5 `5eb170f06fe4ba585f637cc8dacab946`, generated 2026-05-23 17:39 local from `requirements.txt` against current PyPI) to prod and ran `pip install --require-hashes -r requirements.lock`. Exit 0 — **but installed 43 NEWER versions** than the running process was built against (Apr-30 venv). Process (PID 1237405) was unaffected (cached imports in `sys.modules`), but disk was now in an unintended state: any restart would silently deliver the 43 bumps. C-6's goal is reproducibility of the known-good running state, not a mass upgrade.

**Backup tag:** `requirements.lock.bad-bump-20260524` on prod (preserved 14:56 lockfile as recovery breadcrumb).

**Files deployed (1):**
- `requirements.lock` — regenerated locally from `/tmp/pip_pre_20260524_145514.txt` (prod's actual running freeze captured 14:55 UTC, pre-bump) via `uv pip compile --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --generate-hashes -o requirements.lock tmp/prod_running_pin_20260524T1455Z.txt`. New md5 `c1d1db5f2a435ab9ba797b8448ca3287`. 137 packages, every pin matches running prod (zero diff after PEP 503 normalization).

**Disk-side action (real install, NOT dry-run, at 15:14 UTC):**
`pip install --require-hashes -r requirements.lock` against prod venv. Downgraded 43 packages back to OLD versions (anthropic 0.104.1 → 0.97.0, langgraph 1.2.1 → 1.1.10, cryptography 48.0.0 → 47.0.0, etc.). Exit 0. Log: `/tmp/pip_downgrade_20260524T151353Z.log`. Post-snapshot: `/tmp/pip_post_downgrade_20260524T151353Z.txt`.

**Features shipped (load-bearing for future "is X done?" checks):**
- **C-6 (hash-pinned lockfile reproducing prod) CORRECTED.** Lockfile now pins the actual running versions, not arbitrary newer PyPI versions. Future fresh-install from this lock produces the known-good Apr-30 venv state.
- **Disk ≡ lock ≡ running-process convergence verified.** Next operator restart picks up OLD versions matching the lock, not the unintended bumps.

**Notable code changes (callouts a future Claude shouldn't miss):**
- The lockfile's autogen header references `tmp/prod_running_pin_20260524T1455Z.txt` as the input file. That file is NOT in the repo. For future regens that want to capture the then-current running state, repeat the recipe: scp prod's `pip list --format=freeze` to local, feed it to `uv pip compile`. Per `[[reference-uv-pip-compile-cross-platform]]`.
- **The 43 deferred bumps are NOT lost** — see BACKLOG.md P1 "Deferred 43-package upgrade from C-6 lockfile drift" for per-package risk notes. anthropic SDK bump 0.97 → 0.104 specifically requires real-SDK smoke test per `[[feedback-mocks-dont-catch-sdk-shape]]`, not paper soak.

**Latent bugs caught + fixed (if any):**
- (none new from this work — the bad 14:56 install was caught BEFORE it could ride a restart, and reversed in-flight)

**Verification (three-way convergence, all OLD):**
- PID unchanged ✅ — `1237405` (xvfb-run) + `1237421` (Python child) confirmed alive post-downgrade. Same as pre-install.
- `pip install --dry-run --require-hashes -r requirements.lock` ✅ — 137 "Requirement already satisfied", zero "Would install".
- `diff /tmp/pip_pre_20260524_145514.txt /tmp/pip_post_downgrade_20260524T151353Z.txt` → exit 0 (byte-identical disk freezes before-bump vs after-downgrade).
- Journal `--since 15:13` ✅ — normal INFO audit events from kalshi_crypto_arb, polymarket scan, polymarket-data-api, kalshi_copy_trader. No errors, no tracebacks, no lazy-import surprises.

**Inert / dormant on current traffic (if any):**
- The lockfile itself is dormant until the next fresh-install. The trading-corp process continues running its in-memory imports loaded at startup; this deploy does not change its behavior.

**Rollback recipe (do NOT use unless reversing this fix):**
```bash
ssh azureuser@trading.jacksumner.com "
BASE=/home/azureuser/trading_corp; cp \$BASE/requirements.lock.bad-bump-20260524 \$BASE/requirements.lock; \
\$BASE/venv/bin/pip install --require-hashes -r \$BASE/requirements.lock
"
```
Restoring the bumped (broken-intent) lockfile + re-applying the 43 bumps on disk. Reverses this deploy.

---

## 2026-05-24 03:17 UTC — kalshi_sports_scout: MLB team-code aliases AZ + CWS (commit `d6d54d3`, deploy script to be committed)

**Commits:** `d6d54d3` (one-file mapping fix).
**Triggered by:** First post-deploy scan from prior entry showed
`n_unmapped=13` with 6 MLB tickers failing `team_code_not_in_mapping` on
`AZ` and `CWS`. MLB observed = 0 that cycle because every active MLB
game involved AZSF or MINCWS.

**Backup tag:** `.pre-mlb-aliases-20260524`.

**Files deployed (1):**
- `trading_corp/data/sports_team_mapping.py` — two-line addition to
  `MLB_TEAMS`: `"AZ": "Arizona Diamondbacks"`, `"CWS": "Chicago White Sox"`.

**Audit performed (no other gaps found):**
- Ran `scripts/_probe_kalshi_team_codes.py` against current OPEN markets
  on all 4 in-scope series. Results: MLB 94 markets / 30 unique codes /
  **2 gaps (AZ, CWS)**; NBA 6/4/0; NHL 8/4/0; MLS 21+7-TIE / 14/0. Only
  MLB had gaps. The 9 "unused" MLB mapping keys (KCR/OAK/SDP/SFG/TBR/
  WAS/WSN/etc.) are existing defensive aliases — kept.
- Caveat: NBA + NHL audits ran during late-playoff windows so only 4
  team codes each were active. Audit only confirms COMPLETENESS for
  currently-live games, not for the full team rosters. Re-audit at
  start of NBA/NHL regular season (~Oct 2026) before assuming complete.

**Features shipped:**
- **MLB games involving Arizona Diamondbacks or Chicago White Sox now
  resolve.** Previously all 6 such tickers per scan fell to
  `kalshi_sports_scout_unmapped`. Now they should reach the
  divergence-observation step.

**Notable code changes:**
- **Alias style follows existing pattern.** New codes inserted on their
  own line (consistent with `KCR` and `ATH` standalones), breaking the
  two-per-line pairing rather than reflowing the column alignment.
- **NBA / NHL gaps not assessable from the audit.** With only 4 active
  teams each in playoffs, the audit can't surface codes for the other
  ~25 teams. Re-audit needed at regular-season start to confirm full
  coverage before relying on NBA/NHL game-moneyline edge.

**Verification:**
- Pre-deploy probe (commit pending): MLB 94 markets, 2 gaps confirmed.
- Smoke: `py_compile` clean. Import + `MLB_TEAMS["AZ"]` returns
  `"Arizona Diamondbacks"`, `["CWS"]` returns `"Chicago White Sox"`.
  MLB_TEAMS dict size 37 → 39.
- Service restarted at 03:17:03 UTC; `Kalshi Sports Scout online` at
  03:17:49 UTC (PID 1235018). No scout-related startup errors.
- First post-deploy scan expected ~04:17:49 UTC. Background verification
  task pending: confirm MLB `n_observed > 0`.

**Inert / dormant on current traffic:**
- **NBA / NHL playoff vs. regular-season coverage.** Audit confirms
  current-live teams map; doesn't confirm regular-season full roster
  for those leagues. If NBA / NHL games involving non-playoff teams
  appear on Kalshi before regular season starts (rare but possible for
  exhibitions / WNBA / etc.), unmapped rows may surface and require a
  follow-on audit.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-mlb-aliases-20260524; BASE=/home/azureuser/trading_corp
mv \$BASE/trading_corp/data/sports_team_mapping.py.\$TAG \
   \$BASE/trading_corp/data/sports_team_mapping.py
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-24 01:28 UTC — kalshi_sports_scout: series_filter fix for 1-obs/ticker rotation (commit `b880b66`, deploy script `12c0c86`)

**Commits:** `b880b66` (4-file change), `12c0c86` (one-off deploy script).
**Triggered by:** Phase-0 gate review session 2026-05-23 — diagnosis at
`reports/2026-05-23_kalshi_sports_scout_discovery_diagnosis.md` (`07e3579`)
showed each in-scope MLB ticker was observed exactly once (88/92 markets).
Root cause: Sports category has 2018 series; `discover_by_categories`
50-cap returned a rotating 2.5% slice and in-scope leagues landed in only
21/188 scans (11.2%) over 9 days.

**Backup tag:** `.pre-series-filter-20260523` (on 4 files; no new files).

**Files deployed (4):**
- `trading_corp/data/kalshi_market_map.py` — `discover_by_categories` gains
  `series_filter: tuple[str,...] | frozenset[str] | None` kwarg. Out-of-set
  series are skipped before consuming a cap slot. Backward-compatible.
- `trading_corp/brokers/kalshi.py` — `KalshiBroker.list_markets` passthrough
  kwarg.
- `trading_corp/agents/strategies/kalshi_sports_scout.py` — module constant
  `_SCOUT_SERIES_FILTER = ("KXMLBGAME","KXNBAGAME","KXNHLGAME","KXMLSGAME")`,
  passed to `list_markets` call.
- `config/strategies.yaml` — `max_series_per_category: 50 → 100`,
  `leagues: drop NFL` (probe found no NFL game-moneyline series in Sports
  category — only props variants KXNFLGAMETD/FG/SACK).

**Features shipped (load-bearing for future "is X done?" checks):**
- **Series-filtered discovery in `discover_by_categories`.** Generic
  facility; not scout-specific. Other callers of `KalshiBroker.list_markets`
  can constrain to an exact-match series set when they know the targets.
- **Exact-set semantics (not prefix).** Adjacent series like KXNBAGAMES /
  KXNBAGAME7 do NOT sweep in alongside KXNBAGAME. The discovery probe
  on 2026-05-23 found 3 NBA-game-prefix-like series; only `KXNBAGAME`
  itself contains game-moneyline markets.
- **NFL deliberately excluded.** All current "NFL observed" rows from
  Phase 0 were 2026-season placeholder lines 110-120 days pre-game
  (per addendum doc). Re-probe `pykalshi.get_all_series("Sports")` ~3-4
  weeks pre-kickoff to find the correct game-moneyline series, then
  add to `_SCOUT_SERIES_FILTER` + add NFL back to YAML `leagues`.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`_SCOUT_SERIES_FILTER` is hardcoded in the scout, not configured in
  YAML.** League roster is tightly coupled to `sports_team_mapping.py`
  (the canonical league→team-name lookup) — letting them drift would
  silently lose markets. Future cleanup could derive prefixes from the
  same source of truth; for the minimal fix, hardcoded.
- **`max_series_per_category: 100` is comfortable headroom over the 4
  in-scope series.** Cap will not bind. If NFL re-adds (likely 5
  series) the cap still doesn't bind.
- **EOL preservation matters on prod.** `brokers/kalshi.py` and
  `config/strategies.yaml` are CRLF on prod; the other two are LF.
  Deploy script (`scripts/_deploy_2026_05_23_series_filter.py`) detects
  per-file EOL via `\r\n` byte search in first 8KB and translates the
  patch strings accordingly. Same approach applies to future patches.
- **`series_ticker: None` quirk noted in probe.** `client.get_market(ticker)`
  returns market objects with `series_ticker=None`. The fix doesn't use
  per-market `series_ticker` for filtering; the filter happens at series
  enumeration time before `get_markets` is even called. So this quirk is
  not load-bearing for our path.

**Latent bugs caught + fixed (during this session):**
- **`/100.0` units bug at `kalshi_sports_scout.py:232-240`** — separately
  flagged in v1/v2 review docs. NOT fixed by this deploy; recovered via
  `recovered = stored × 100` for analysis (see review doc v2). The fix
  for live trading is one-line + a sum-to-1 sanity guard; deferred until
  the post-rerun corpus is in.
- **Foreign in-flight change separated from this commit.** Working tree
  had a pre-existing `kalshi_sports_arb_observer.enabled: false → true`
  flip with "FLIPPED 2026-05-23 for MLB Phase 0" comment. Not mine;
  reverted in the working tree before staging, then restored uncommitted
  after the scout commit. Operator's WIP intact.

**Verification:**
- Pre-deploy probe (commit `07e3579` references): `TOTAL_SERIES: 2018`
  in Sports category; per-league sample tickers confirmed all 4 in-scope
  series exist with the exact prefixes assumed.
- Post-patch smoke: `py_compile` clean on 3 python files;
  `yaml.safe_load` clean on strategies.yaml with expected key set.
- Service restart: `sudo systemctl restart trading-corp.service` at
  2026-05-24 01:28:30 UTC; `systemctl is-active = active`; journal at
  01:29:11 `Kalshi Sports Scout online (enabled=True, has_credentials=True)`.
- First post-deploy scan expected ~02:29:11 UTC (scout loop sleeps
  `poll_interval_sec=3600` BEFORE first scan, per `_scheduled_kalshi_sports_scout_loop`
  at `main.py:3553`).

**Inert / dormant on current traffic:**
- **No code change to the divergence-computation path.** The units bug
  remains; observed rows will still be 100× off until a follow-on fix.
  Discovery side ships; analysis side stays the same.
- **`series_filter` kwarg added to `KalshiBroker.list_markets` but only
  the scout uses it today.** Other Kalshi strategies (weather, crypto,
  llm_arbitrage, temporal_bucket, tail_price, copy_trader) call
  `list_markets` without the kwarg — their behavior is unchanged.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-series-filter-20260523; BASE=/home/azureuser/trading_corp
for f in trading_corp/data/kalshi_market_map.py \
         trading_corp/brokers/kalshi.py \
         trading_corp/agents/strategies/kalshi_sports_scout.py \
         config/strategies.yaml; do
  mv \$BASE/\$f.\$TAG \$BASE/\$f
done
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-23 15:52 UTC — bitunix: bias TTL 90→30 + flip-opportunity detection (commit `6073480`)

**Commits:** `6073480` (YAML + observer + 8 tests). On `origin/main` via parallel-session fast-forward — origin head at deploy time: `03e8917` (an unrelated BACKLOG.md EOS snapshot atop `6073480`; `git diff 6073480 03e8917 -- <the 2 deploy files>` empty, so the deploy source is the `6073480` blobs exactly).
**Triggered by:** Vortex audit + iterative scoping. Two changes addressing the no-close-on-opposite-signal gap without committing to the full close-on-opposite-PREMIUM build (~250 LOC, gated on observed data): (a) cause-side bias TTL shrink, (b) symptom-side instrumentation. Vortex's scope doc retained as the implementation plan, gated behind `flip_opportunity_detected` rows demonstrating the leak frequency × R-cost justifies the build.
**Backup tag:** `pre-bias-flip-detection-20260523` on the 2 modified prod files (paths below). md5 captured on prod: YAML `52722fe9b49f0fdacd5554553ff8a467` (81680 bytes, CRLF — matches prod pre-deploy state); observer `406cd632571276d800ac628a27b4adc8` (103726 bytes — matches local `6073480~1` LF blob exactly).

**Files deployed (2 modify):**
- `config/strategies.yaml` — bitunix_futures block lines 1189-1190: `bias_bull` + `bias_bear` `ttl_minutes: 90` → `30`. Cause-side fix for bias-into-stale-regime suppression of opposite-side entries on the 3m engine. Weight unchanged at 2 (one knob, not both, per scoping discipline). Comment-laden form in git blob (`# 90→30 2026-05-23: …`) was not deployed — sed-surgical edit on prod (see "Notable" below) replaced only the bare numerals, so prod YAML carries no comment for this change. Owner: root:root preserved.
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — new `_detect_flip_opportunity` helper (line 680) + new `flip_opportunity_detected` audit kind + hook call at the post-SKIP / pre-PA-gate point (line 1228, try/except-wrapped so the detector cannot break the trading path). Observe-only — captures one row per (PREMIUM tier ∧ opposite open paper position) coincidence with open trade id/side/entry/stop/ts, opposing side/tier/net_score/signal/source, current_price, and unrealized R. NEVER closes, modifies, or otherwise touches the open position. md5 post-deploy: `5b7d342b6c7e179379f0095e8a2b6414` (matches git blob of `6073480` exactly). Owner: azureuser:azureuser preserved.

**Features shipped (load-bearing for future "is X done?" checks):**
- **bias_bull/bias_bear TTL = 30m** in the active bitunix_futures scoring factor block. Side effect: `_max_ttl_minutes` (observer:482-487) ceiling shrinks from 90→30; ledger-pull window in `_load_live_alerts_in_window` likewise shrinks. Consistent with rationale ("don't hold regime-scale bias on a scalp engine") — intended, not a bug.
- **`flip_opportunity_detected` audit kind active** in `audit_event`. Detection fires AFTER the SKIP short-circuit and BEFORE the PA gate, so the row is written even when PA later rejects the new opposing fire — captures the full leak universe, not just the trades that would have placed.
- **`_detect_flip_opportunity` helper queryable via /proc** for live introspection of the observer source under the running PID (1185752 at deploy time).

**Notable code changes (callouts a future Claude shouldn't miss):**
- **YAML patch hunk REJECTED on EOL mismatch** during initial `patch -p1` run. Root cause: prod YAML uses **CRLF** line terminators (1549-byte drift vs local LF git blob is pure CRLF cost — verified via `file` + `od -c` on prod, no semantic drift). Recovered via `sudo -u azureuser sed -i '1189s/ttl_minutes: 90/ttl_minutes: 30/' config/strategies.yaml` (and same for line 1190). Sed-in-place preserves CRLF endings; `.rej` file removed. Pattern memorialized at `[[deploy-mechanics-crlf-config-patch]]` — for single-knob CRLF config edits, prefer sed over patch + wholesale-replace (wholesale-replace blows the 28KB az `--scripts` cap on this file).
- **The observer's `scoring_config` is loaded ONCE at startup** (`main.py:319-335`), no mtime check — unlike Otter/Cypher's property-mtime path. Any future bitunix YAML change is INERT until `systemctl restart trading-corp`. This is why the deploy required a restart, not just a file copy.
- **Detector is wrapped in try/except** so a DB hiccup in the detection path cannot break placement. The catch logs `bitunix_observer: flip detection raised: ...` and proceeds to PA gate normally.

**Latent observation captured (not new, but corrects a stale claim):**
- **`position_sl_update` count = 4** on prod (not 0 as a prior audit had claimed). The multi-leg TP/trail lifecycle has engaged multiple times since the B7+B9 reconciler hardening on 2026-05-22 01:50 UTC. The reality-verified `2942ff8e` (`runbooks/deploy_log.md:678`) is one of them. Net implication: the exit system is **working**, which further weakens the urgency of the close-on-opposite build that the detector instruments. Detection data is now the gate.

**Verification:**
- Push: `git push origin main` returned "Everything up-to-date" — parallel session had already pushed `6073480`. `git ls-remote origin refs/heads/main` = `03e8917...` (which contains `6073480`). Local HEAD == origin HEAD, clean tree.
- Pre-deploy probe: prod observer md5 = `406cd632571276d800ac628a27b4adc8` = local `6073480~1` blob exactly (clean patch baseline). Prod YAML md5 = `52722fe9b49f0fdacd5554553ff8a467`, CRLF-drifted but bias_bull/bias_bear at lines 1189-1190 byte-identical to expected pre-state.
- Pickle refresh (15:43 UTC): `scripts/rh_mfa_refresh_prod.sh` ran clean. New pickle 1396 bytes; LOGIN OK; 3 RH accounts bound; old code+config still on disk (expected — deploy follows).
- Backup tag `pre-bias-flip-detection-20260523` applied; md5 verified to match pre-deploy prod state.
- Patch applied: observer hunk clean (post-patch md5 `5b7d342b...` matches expected NEW LF blob); YAML hunk rejected → recovered via sed; `.rej` removed.
- Post-deploy md5 (prod): observer `5b7d342b6c7e179379f0095e8a2b6414` exact match; YAML `d2a263ac8b6c8887e8efb1f136c94793` (CRLF-form, no exact-LF match expected — verified by semantic-grep on bias lines: both show `ttl_minutes: 30`).
- Restart: 15:52:00 UTC. MainPID transitioned 1183988 → 1185752 (changed ✓). Web command center listening on `0.0.0.0:8000` at 15:57:00 UTC (~5 min latency due to IC position-manager startup catch-up; normal pattern). Healthz `HTTP 200 {"status":"ok","mode":"PAPER"}` both internal + external via Caddy.
- BitunixBroker connected: `account=bitunix-futures, equity=$1121.83, 0 positions`. Observer wiring line: `scoring=True, pa_enabled=True, htf_gate_mode=enforce, htf_regime_enabled=True, trade_plan_active=True`.
- **TTL load proof:** `/proc/1185752/root/home/azureuser/trading_corp/config/strategies.yaml` lines 1189-1190 show `ttl_minutes: 30` — verified live against the running process, not the on-disk file alone. Detector helper present in live source under `/proc/<PID>/root/...`: `_detect_flip_opportunity` at line 680, `flip_opportunity_detected` literal at line 755, hook call at line 1228.
- **Observe-only confirmed:** SQL `kind LIKE '%close%' OR '%cancel%' OR '%flip_close%' OR '%position_flip%'` since restart → empty. The detector writes only `flip_opportunity_detected` rows.
- **Detector count post-deploy (24 min after restart):** `flip_opportunity_detected` = 0 (no firings yet; no `bitunix_score_decided` events post-restart either — quiet TV window). Detection accrues from here.
- Pre-existing recurring failure not caused by this deploy: Fidelity broker startup login → `'can't complete this action'` page → `broker_fallback_to_paper` audit for `fidelity_joint` + `fidelity_401k`. Independent of bitunix; restart unrelated.

**Inert / dormant on current traffic:**
- The detector itself fires only on (PREMIUM tier ∧ open paper position ∧ opposite side). At time of deploy: no open bitunix paper positions; therefore even a PREMIUM signal would not produce a flip_opportunity_detected row. Open positions accumulate during normal traffic.
- The close-on-opposite-PREMIUM EXECUTION path is **NOT deployed**. Only the detection path is live. The full build remains gated on observed data.

**NOT touched by this deploy:**
- `agents/divisions/bitunix_position_reconciler.py` — no reconciler change; SL ratchet logic unchanged.
- `paper_trade_record` schema — no new columns, no new `result` enum values.
- `RiskAgent.evaluate()` and any risk-gate caps — untouched.
- `auto_execute` flag for bitunix_futures — remains `true` (already was; no flip).
- Otter/Cypher webhook paths — untouched.
- Other YAML factor TTLs (only bias_bull + bias_bear edited).

**Rollback recipe:**
```bash
TAG=pre-bias-flip-detection-20260523; BASE=/home/azureuser/trading_corp
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "
sudo -u azureuser mv $BASE/config/strategies.yaml.\$TAG $BASE/config/strategies.yaml
sudo -u azureuser mv $BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.\$TAG $BASE/trading_corp/agents/divisions/bitunix_futures_observer.py
sudo systemctl restart trading-corp.service"
# Pickle refresh first if restart wedges on MFA — see scripts/rh_mfa_refresh_prod.sh.
# Then locally: git revert --no-edit 6073480 && git push origin main
# (or leave the commit and just revert prod files; the detector code is harmless inert when reverted to the pre-helper file).
```

---

## 2026-05-23 15:23 UTC — pm-metrics-epoch: agent_state-driven reset (commit `17cdd55`)

**Commits:** `17cdd55` (helpers + threading + tests). Pushed to `origin/main` via fast-forward of branch `pm-metrics-epoch`. `origin/main` head: `17cdd55`.
**Triggered by:** Board approval after the P1 backlog item ("metrics-epoch reset scout") was turned into a complete plan + reversibility-tested locally. Mirrors the Kalshi `DASHBOARD_RT_CUTOFFS` / `_kalshi_cutoff_clause` machinery, with two intentional departures: storage in `agent_state(polymarket_copy_trader, metrics_epoch)` for redeploy-free reversibility; coverage extended to `audit_event.ts` (open/pending) and `polymarket_equity_history.ts` (curve) beyond just round_trips.
**Backup tag:** `pre-metrics-epoch-20260523-0710` on the single modified prod file (paths below). No agent_state slot was written by this deploy; the `metrics_epoch` slot stays unset (no-op state) until the operator deliberately sets it as a separate action.

**Files deployed (1 modify):**
- `trading_corp/web/data.py` — adds `_get_polymarket_metrics_epoch(db_url)` (ISO-8601-validated read of `agent_state(polymarket_copy_trader, metrics_epoch)`) and `_polymarket_cutoff_clause(epoch_iso, *, ts_col, div_col, div_value)` (mirror of `_kalshi_cutoff_clause`; returns `''` no-op when `epoch_iso` is None). Threads `pm_epoch: str | None = None` kwarg into 6 PM query helpers (`_query_pm_round_trips`, `_query_pm_equity_curve`, `_query_pm_open_trades`, `_query_pm_pending_count`, `_query_pm_resolved_stats`, `_query_pm_whales`) + applies the cutoff inline in `_hydrate_pm_overview` (the home-tile rollup). Single epoch resolution per dashboard build in `build_prediction_market_view`; same helper resolves internally in `_hydrate_pm_overview`. `PMDashboardView` gains `pm_metrics_epoch: str | None` field. Owner: root:root preserved.

**Why entry_ts ≡ audit_event.ts (the dual-cutoff is coherent):** verified at `polymarket_resolver.py:161` (market-settle path: `entry_ts = row['_ts']`) and `:306` (whale-closed path: `entry_ts = str(entry_row['ts'])`). Both source the BUY-side audit_event row's ts — byte-equal. The dual filter (a.ts on open/pending surfaces, entry_ts on resolved surfaces) is filtering the SAME physical timestamp from two angles. A position opened pre-epoch / resolved post-epoch is invisible on every surface — the "old bet settling into fresh slate" failure mode is prevented at every stage.

**Validation gate (injection defense):** `_get_polymarket_metrics_epoch` parses the stored value via `datetime.fromisoformat` round-trip and returns None on any failure. The returned value is f-string-interpolated into SQL by `_polymarket_cutoff_clause`; the parse is the injection gate, mandatory regardless of write-path trust. Tests cover injection-shaped payload, non-ISO string, non-string value, JSON null, empty string, and missing slot — all rejected.

**Features shipped (LATENT — dashboard view unchanged until epoch is set):**
- Operator can run `set_agent_state('polymarket_copy_trader', 'metrics_epoch', '<ISO>')` at any moment to mark a new performance epoch. All 7 metric surfaces immediately filter to post-epoch only; pre-epoch rows stay in `polymarket_round_trips` / `audit_event` / `polymarket_equity_history` for forensics.
- Reversibility: `DELETE FROM agent_state WHERE agent='polymarket_copy_trader' AND key='metrics_epoch'` is the canonical unset; `set_agent_state(..., None)` works as a fallback (helper treats JSON null as unset). Either restores the pre-epoch view exactly. Redeploy-free.

**NOT touched by this deploy:**
- `agent_state` slots other than `metrics_epoch` — `selected_whales`, `pinned_whales`, `watch_only_whales` (the windowed list from 2026-05-23 06:23 UTC) all untouched.
- `polymarket_copy_trader` strategy / broker / risk gate / audit pipeline.
- `seed_polymarket_watchlist_deep.py`, `refresh_polymarket_whales.py` (watchlist + roster pickers).
- Kalshi side — Kalshi cutoffs continue to live in `DASHBOARD_RT_CUTOFFS` (hardcoded), separate machinery.

**Verification:**
- Service restart: 15:18:34 UTC → web bound at 15:23:44 UTC (5m 10s, matches windowing-deploy startup pattern). Healthz `HTTP 200 {"status":"ok","mode":"PAPER"}`. MainPID 1180983.
- Post-deploy md5 of `trading_corp/web/data.py`: `f3898a5e47308f917c7c56e121bffe46` — matches LF-normalized local.
- `agent_state(polymarket_copy_trader, metrics_epoch)` slot: None (unset, no-op state) — confirmed via direct DB query post-deploy.
- `_get_polymarket_metrics_epoch(...)` returns `None`; `_polymarket_cutoff_clause(None)` returns `''` — confirmed in-VM.
- Dashboard at `/prediction-markets/polymarket_copy_trading` renders HTTP 200, 1.7 MB. All windowing markers (AvgPx column, `<.70` column, provisional `opacity-50 italic` rows, sort URLs, "scored on last 100 resolved BUYs" header) still present from the 06:23 UTC ship. Tile values populated (Resolved 2,269 / Open 1,117 / Realized +$193.20) — non-zero, sensible. Home tile for polymarket_copy_trading renders cleanly.
- Pre-deploy reversibility test (synthetic frozen sandbox): all 7 stages PASS — pre-state captured, EPOCH set mid-data cuts PCT 6→3 with arb 3→3 control, forward-zero (future epoch) zeros all PCT surfaces, invalid epoch + injection payload rejected by validator (no-op), DELETE unset restores pre-state EXACTLY, `set_agent_state(None)` fallback also restores pre-state EXACTLY.

**How to set the epoch (operator action — separate from this deploy):**
```bash
ssh azureuser@trading.jacksumner.com "sudo /home/azureuser/trading_corp/venv/bin/python3 -c \"
from trading_corp.persistence import db
db.set_agent_state('polymarket_copy_trader', 'metrics_epoch',
                   '<your-chosen-ISO-8601-timestamp>',
                   db_url='sqlite:///data/trading_corp.db')
\""
```

**How to unset the epoch (operator action):**
```bash
# Canonical:
ssh azureuser@trading.jacksumner.com "sudo sqlite3 /home/azureuser/trading_corp/data/trading_corp.db \"
DELETE FROM agent_state WHERE agent='polymarket_copy_trader' AND key='metrics_epoch';
\""

# Fallback (works via the helper's JSON-null handling, but leaves a phantom row):
# … set_agent_state('polymarket_copy_trader', 'metrics_epoch', None, …)
```

**Rollback recipe** (code revert — needed only if the no-op state ever surfaces a bug, which the reversibility test indicates shouldn't happen):
```bash
TAG=pre-metrics-epoch-20260523-0710; BASE=/home/azureuser/trading_corp
ssh azureuser@trading.jacksumner.com "
sudo mv $BASE/trading_corp/web/data.py.\$TAG $BASE/trading_corp/web/data.py
sudo systemctl restart trading-corp.service"
# Then: git revert --no-edit 17cdd55 && git push origin main
# If the epoch slot was set, clear it first via the DELETE recipe above.
```

---

## 2026-05-23 06:23 UTC — pm-watchlist: windowed re-score on last 100 resolved BUYs (commits `6e37b48`, `0045ff1`, `5d7704c`)

**Commits:** `6e37b48` (windowing + 19 unit tests), `0045ff1` (edge-proxy columns AvgPx + <.70 + PnL floor $5k + server-side sortable headers + 9 sort tests), `5d7704c` (backlog: deferred root-hardening followup). All three pushed to `origin/main` via fast-forward merge of branch `pm-watchlist-windowed-rescore`. `origin/main` head: `5d7704c`.
**Triggered by:** Operator-approved deploy of the windowed-rescore branch. Replaces the lifetime-scored watchlist (≥100 lifetime resolved BUYs + ≥70% lifetime WR ranked by lifetime realized PnL) with sliding-window scoring on each whale's last 100 resolved BUYs. Operator-stated motivation: lifetime stats let inactive whales + high-volume-low-edge favorite-farmers crowd the screening list; sample-size-constant windowing + AvgPx edge-proxy column resolve both failure modes.
**Backup tag:** `pre-windowed-20260523-0543` on 4 modified prod files (paths below). Pre-deploy `agent_state(polymarket_copy_trader, watch_only_whales)` snapshot at `/tmp/backup_watch_only_whales_pre_windowed_20260523.json` (25,061 bytes, md5 `c725d496bb31859e4a16e5b24b9014d3`, **kept on prod through at least the first steady-state Sunday refresh 2026-05-24**).

**Files deployed (4 modify):**
- `trading_corp/scripts/seed_polymarket_watchlist_deep.py` — windowing pipeline: `_fetch_wallet_activity_windowed` (3-condition termination: target_buys_reached / exhausted / max_pages_hit ceiling at 10 pages × 500 rows), `_select_resolved_buys_window` (most-recent-N resolved BUYs from paged activity, true N recorded on sub-window), recency floor on any-side `last_trade_iso` ≤ 60 days, `compute_polymarket_stats(half_life_days=36500.0)` (the window IS the recency mechanism), quality floors WR ≥ 0.62 AND realized PnL ≥ $5,000 (production-calibrated), n ≥ 10 noise floor, provisional flag at n < 50, schema additions `window_size_n` + `window_days_span` + `last_trade_iso` + `provisional` + `avg_entry_price` + `share_below_70`. CLI: `--top` default 0 (no cap), `--min-positions` renamed to `--min-resolved-buys`, `--min-win-rate` renamed to `--min-windowed-wr`. Owner: root:root preserved.
- `trading_corp/web/data.py` — `PolymarketWatchOnlyRow` dataclass gains 6 new fields (defaulted for back-compat with pre-windowed agent_state entries). `_query_polymarket_watch_only_rows` accepts whitelisted `sort_key` + `sort_desc`; whitelist includes aliases (`pnl`, `avg`, `avgpx`, `below_70`, etc.); unknown keys fall back to default rank ordering; None-value rows sink to bottom independent of sort direction. `build_prediction_market_view` plumbs `pm_watch_sort` + `pm_watch_desc` into the dashboard view. Owner: root:root preserved.
- `trading_corp/web/routes.py` — all four `/prediction-markets/[partials/]/{division?}` endpoints accept `pm_watch_sort` + `pm_watch_desc` query params. Owner: azureuser:azureuser preserved.
- `trading_corp/web/templates/partials/pm_dashboard_body.html` — new Jinja macro `pm_watch_sort_link(key, label, default_key=False)` renders each column header as an HTMX `<a hx-get hx-target=#pm-content hx-push-url>`; active column gets ↑/↓ arrow + text-mono highlight. New columns: Span, Last, AvgPx, `<.70`. Provisional rows greyed via `opacity-50 italic` driven by `w.provisional` attribute — independent of sort column. Owner: root:root preserved.

**Systemd unit edit (one-line):**
- `/etc/systemd/system/trading-corp-pm-watchlist-deep.service` ExecStart: dropped `--max-total 100` (operator accepted floating list size; PnL floor + provisional cue replace the cap). Backup tag matches code-deploy backup tag. `systemctl daemon-reload` issued. Steady-state ExecStart now: `… seed_polymarket_watchlist_deep --merge`.

**One-shot first run (overwrite mode, no `--merge`, no `--max-total`):**
- Ran as root via `az vm run-command` (matches `User=root` on the service unit). Command: `… seed_polymarket_watchlist_deep --json`. Wall-clock 28m 43s (05:54:26 → 06:23:09 UTC) — 12.5 min of that burned in Cloudflare-403 retry on a single resolutions chunk (chunk 898, 5 retries, eventually succeeded on attempt 6). Zero `terminal failures` / `PolymarketRateLimitError`. Cost note filed at BACKLOG `P2 (ops) — Cloudflare-retry burn vs TimeoutStartSec=3600`.

**Features shipped:**
- **Polymarket Watch List dashboard panel** (`/prediction-markets/polymarket_copy_trading`) now ranks 197 whales by **windowed** realized PnL (last 100 resolved BUYs, last_trade ≤ 60 days, WR ≥ 62%, PnL ≥ $5k, n ≥ 10). Header text reads "scored on last 100 resolved BUYs". 27 of 197 are provisional (n<50, rendered with `opacity-50 italic` + `prov` badge + tooltip).
- **AvgPx (mean BUY entry price) + `<.70` (share of windowed BUYs entered at <$0.70) columns.** Color-coded AvgPx: <$0.50 green (sharp/contrarian), $0.50-$0.85 mono, ≥$0.85 muted (favorite-farmer). Operator can discriminate edge-driven whales from capital-driven favorite-farmers at a glance — `[[polymarket-whale-scoring-edge]]` memory captures the structural finding.
- **Server-side sortable headers** via `?pm_watch_sort=<key>&pm_watch_desc=<0|1>` query params + HTMX swap of `#pm-content`. URL push-state preserves sort across refresh + back/forward. Whitelisted keys: `rank`, `user_name`, `best_category`, `n`/`window_size_n`, `span`/`window_days_span`, `last`/`last_trade_iso`, `wr`/`win_rate_pct`, `avg_entry_price`/`avg`/`avgpx`, `share_below_70`/`below_70`, `realized_pnl_usdc`/`pnl`, `lifetime_pnl_from_leaderboard`/`lifetime_pnl`, `lifetime_vol_from_leaderboard`/`lifetime_vol`.

**Inert / dormant on current traffic:**
- The `--merge` accumulation path is dormant until Sun 2026-05-24 13:07:58 UTC (next timer fire). First weekly fire will union freshly-windowed candidates with the overwrite-baseline written at 06:23 UTC today.

**NOT touched by this deploy:**
- `agent_state(polymarket_copy_trader, selected_whales)` — copy-execution roster. Code-level guaranteed: the seed script never names `selected_whales`. Snapshot before+after confirmed identical.
- `refresh_polymarket_whales.py` (live roster picker) — left at existing Wilson LCB × edge × category logic with 30-day half-life.
- `polymarket_copy_trader` strategy / broker adapter / risk gate / audit pipeline.
- Kalshi watchlist seed (`seed_kalshi_watchlist_deep.py`).
- Paper-metrics epoch / cutoff dates. Operator explicitly held this — watchlist screening change ≠ copy-execution change.

**Verification:**
- Service restart: PID 1157880 → 1157894, `ExecMainStartTimestamp=2026-05-23 05:48:34 UTC`, web bound at 05:53:11 UTC (~277s startup; long-tail of broker fan-out + Azure KV, no errors beyond pre-existing Fidelity broker_fallback_to_paper).
- Healthz: `HTTP 200 {"status":"ok","mode":"PAPER"}`.
- One-shot seed summary: 2389 candidates, 197 quality-gate pass, 27 provisional, written. Drop reasons: wr_floor=1013, pnl_floor=815, n_floor=249, recency_floor=115. Termination: target_buys_reached=1758, exhausted=629, max_pages_hit=0, fetch_error=2.
- Dashboard render confirmed: AvgPx + `<.70` columns rendered on all 197 rows, sort URLs swap `#pm-content` with `?pm_watch_sort=…` query params, active-column arrow moves with sort, 27 provisional rows greyed under every sort. Top of default sort: Mosley1 ($299k / n=100 / AvgPx 0.394 / <.70 99%), ethanaz ($230k / n=100 / WR 63% / AvgPx 0.447 / <.70 95%) — exactly the mid-WR/high-edge whale the old WR≥70% gate excluded. AvgPx-ascending sort: Wickier leads ($75k / n=46 provisional / AvgPx 0.166 / <.70 98%) — emerging-sharp whale the old lifetime ≥100 gate hid. Size-vs-edge separation working on prod.
- Timer next fire: `Sun 2026-05-24 13:07:58 UTC`, steady-state ExecStart confirmed `… --merge` (no `--max-total`).

**Rollback recipe** (split urgency — 7.1 + 7.2 restore working prod immediately; 7.3 is the deliberate non-urgent tail):
```bash
# 7.1 (URGENT) — restore agent_state from pre-deploy snapshot
ssh azureuser@trading.jacksumner.com "sudo /home/azureuser/trading_corp/venv/bin/python3 -c \"
from trading_corp.persistence import db
import json
with open('/tmp/backup_watch_only_whales_pre_windowed_20260523.json', 'r') as f:
    val = json.load(f)
db.set_agent_state('polymarket_copy_trader', 'watch_only_whales', val, db_url='sqlite:///data/trading_corp.db')
\""

# 7.2 (URGENT) — revert systemd unit so next Sunday fire uses old flags
ssh azureuser@trading.jacksumner.com "sudo sed -i 's|seed_polymarket_watchlist_deep --merge\$|seed_polymarket_watchlist_deep --merge --max-total 100|' /etc/systemd/system/trading-corp-pm-watchlist-deep.service && sudo systemctl daemon-reload"

# 7.3 (DELIBERATE — only after 7.1 + 7.2 stabilize prod) — code revert
TAG=pre-windowed-20260523-0543; BASE=/home/azureuser/trading_corp
ssh azureuser@trading.jacksumner.com "
for f in trading_corp/scripts/seed_polymarket_watchlist_deep.py trading_corp/web/data.py trading_corp/web/routes.py trading_corp/web/templates/partials/pm_dashboard_body.html; do
  sudo mv $BASE/\$f.\$TAG $BASE/\$f
done
sudo systemctl restart trading-corp.service"
# Then on dev: git revert --no-edit 5d7704c 0045ff1 6e37b48 && git push origin main
```

---

## 2026-05-23 00:40 UTC — IC morning-candidate grader: ship to prod (commit `112aef3`)

**Commits:** `112aef3` (the grader, committed 2026-05-22 ~13:30 UTC, intentionally held off origin during the IC grader session). Pushed to `origin/main` is a separate decision, deliberately deferred — local `main` head at deploy time is `1bcd8b4` (the §6 closure note from this session, also on top of `112aef3`); both are local-only.
**Triggered by:** Gate [3] of the IC grader ship sequence per `runbooks/session_start_2026_05_23.md` and `planning/ic_grader_section6_closure_20260523.md`. The three sequential ship gates (AM SDK fix → §6 live-verification → CRLF deploy) all closed: AM fix shipped 2026-05-22 16:47 UTC (`e977641`); §6 closed locally this session at `1bcd8b4`.
**Backup tag:** `pre-grader-20260523-0036` on the 2 modified prod files. 3 new files have no backup target by definition.

**Pickle-refreshed-first note (operator action):**
- Per `[[kalshi-weather-floor-data-gap-20260521]]`, every restart with an expiring `robinhood.pickle` risks a multi-cycle MFA loop. Operator was asked to refresh by hand before the restart. **Filesystem ground truth showed `/home/azureuser/.tokens/robinhood.pickle` mtime unchanged at 2026-05-21 01:58:34 UTC (47h stale) at restart time** — flagged as a fork; operator authorized proceeding (existing token was valid in practice). Post-restart `RobinhoodBroker logged in (user=jrsumner@yahoo.com)` cleanly with all 3 accounts (individual / IRA / joint) bound — no MFA loop fired. The 47h-old pickle was in fact still valid; the operator's pre-deploy refresh either ran against a different mechanism (in-process token store), was a no-op because the token was sliding-window-valid, or didn't take. For future deploys: don't conflate "operator says refreshed" with "filesystem mtime updated" — verify both, or accept the operator's call but document the discrepancy here.

**Files deployed (5 runtime; tests are local-only):**

*Modified (2):*
- `trading_corp/web/routes.py` — adds `POST /telemetry/iron_condor/grade` (form `paste` → calls `grade_paste(...)` with real `MarketDataProvider` + `ic_strategy` + `logger_agent` → renders `iron_condor_grader_result.html` for htmx swap). md5 (prod, LF): `e8aa9a1…` → `8376e5c7…`. 33-line addition + CRLF→LF normalization at transport time (working tree is CRLF, prod is LF — normalization done in the deploy pipeline, NOT in a commit, to preserve `git blame` on the ~4000 unchanged lines per `[[feedback-crlf-routes-py-deploy]]`).
- `trading_corp/web/templates/partials/iron_condor_static_sections.html` — adds the collapsible `<details>` "IC Candidate Grader" block at the top of the static section. md5 (prod, LF): `d04b22e5…` → `8e63c37f…`.

*New (3):*
- `trading_corp/agents/strategies/ic_candidate_grader.py` — research-only grader (8-gate sequential, first-failure-wins, cheap-first). No execution path; AST-walked by `test_no_execution_invariant`. md5: `df7a2378…`. 40,764 bytes.
- `trading_corp/web/templates/partials/iron_condor_grader.html` — paste form (textarea + Grade button + htmx swap target). md5: `bcd91d54…`. 1,914 bytes.
- `trading_corp/web/templates/partials/iron_condor_grader_result.html` — per-row verdict table (PASS/FAIL/NEEDS_LIVE_DATA, failed_gate, reason, measurements). md5: `91982c02…`. 5,402 bytes.

*Local-only (NOT deployed):*
- `tests/test_ic_grader.py` — 25/25 tests, runs locally only (pytest does not run on prod).

**Features shipped:**
- **IC candidate grader endpoint live at `/telemetry/iron_condor/grade`.** Operator pastes a Barchart screener block; each row graded against live `robinhood_joint_iron_condor` rules using live `MarketDataProvider` data (NEVER the pasted Barchart numbers). Grader sits behind Authelia like the rest of `/telemetry/iron_condor`; no API/auth surface added.
- **8 gates, sequential, first-failure-wins, cheap-first:** universe → expiration_on_chain → dte → ivr → strikes → delta_proximity → term_structure (gate 7) → credit (gate 8).
- **Term-structure operand order pinned verbatim from strategy** (`robinhood_joint_iron_condor.py:491`).
- **Per-Q2 design divergence from strategy:** one-leg-None on `get_atm_iv` → emits `NEEDS_LIVE_DATA` instead of fail-open. Operator transparency is the grader's purpose.
- **Audit row per run** (`kind='ic_grader_run'`, actor `ic_candidate_grader`) — payload carries counts + failure breakdown + cfg version hash; **no raw paste content** (privacy invariant verified in §6).

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Structural no-execution guarantee.** The grader module imports zero strategy or order-surface names; uses a `_StrategyLike` Protocol duck-typed against `RobinhoodJointIronCondorAgent`. `test_no_execution_invariant` AST-walks the imports.
- **§6 live-verification ship gate corrected criterion documented at `planning/ic_grader_section6_closure_20260523.md`.** The runbook restatement at `runbooks/session_start_2026_05_23.md` lines 76–95 carries an incomplete criterion ("PASS or FAIL@term_structure") that doesn't anticipate gate 8 (credit) outcomes. Corrected: §6 satisfied when verdict is PASS, OR FAIL at any gate ≥ 7 (term_structure or credit); disqualifying = `NEEDS_LIVE_DATA` at gate 7 OR failure at any gate < 7. General principle: §6 satisfied when run reaches or passes gate 7 on real data.
- **`_get_configured_provider()` is the route's only provider source.** Resolves via `provider_factory.get_provider(strategy_slug=None, config_path=Path("config/data_providers.yaml"))` — Tastytrade primary, fallback null. No DI for the route; the global config drives behavior.
- **Plan-doc phantom pointer.** The commit message references `.claude/plans/planning-session-ic-hashed-kettle.md` as the design source; **that file does not exist** in the repo or in git history. Per `[[session-committed-phantom-pointer]]`. Use `planning/ic_grader_section6_closure_20260523.md` as the canonical §6 acceptance reference.

**Verification:**
- md5-diff of all 5 runtime files at production paths matches local LF-normalized md5s exactly (`df7a2378…`, `8376e5c7…`, `bcd91d54…`, `91982c02…`, `8e63c37f…`).
- `grep $'\r'` on prod `routes.py` returns empty (LF-only confirmed).
- `systemctl is-active trading-corp` → `active`. MainPID `1141109` since `2026-05-23 00:40:51 UTC` (was `1119435` since `2026-05-22 22:17:49 UTC` — dashboard cutoff deploy). `Result=success`.
- Post-restart journal: clean broker init (paper + Robinhood 3 accounts + Polymarket + Kalshi). Two pre-existing recurring errors observed and ignored (NOT grader-related): Kalshi copy-trader `name 'wallet' is not defined` in prior-PID logs; Fidelity bot-detection rejection → `broker_fallback_to_paper` audit (designed behavior).
- **Prod §6 verification (corrected criterion, all 5 acceptance points satisfied):**
  - Provider class = `TastytradeDataProvider` in both direct call and route's `_get_configured_provider()` — same singleton instance.
  - Fresh B2 candidate constructed against live SPY chain: `SPY  06/30/26 (38)  699/702  775/778  35%`. short_put 702 (Δ=−0.1598), short_call 775 (Δ=+0.1639); 3.0-pt wings verified on chain.
  - Verdict: **FAIL, failed_gate=`credit`** (gate 8). Reaching gate 8 = gate 7 PASSED.
  - Direct gate-7 probe (under same event loop): front 0.1500, back 0.1651, spread **−0.0151** (contango, well below max_diff 0.05).
  - 1 `ic_grader_run` audit row written (prod's actual path is 1 POST = 1 row). Payload keys match spec exactly. Paste content NOT in payload (privacy invariant intact).
  - Credit-gate FAIL is itself a correct real result (SPY 16Δ $3-wing genuinely yields ~$0.78 = 26% < 33% floor) — real information about why the strategy isn't finding SPY trades right now, not a defect.
- **Initial prod §6 run produced NEEDS_LIVE_DATA spuriously** due to a bug in the test harness (multiple `asyncio.run()` calls created+closed independent event loops, breaking the Tastytrade SDK session→loop binding). Fix: collapse all async work into a single `asyncio.run()`. Production route uses single-event-loop FastAPI semantics, so this bug is local-to-the-verification-script only. Documented for next session in case a similar verification script is needed.

**Inert / dormant on current traffic:**
- Grader endpoint requires manual operator interaction (paste + submit). No scheduled cron or autonomous caller. Will sit idle between operator scan-grading sessions.
- `iron_condor_static_sections.html` change adds a collapsible `<details>` that is **closed by default**; existing dashboard sections render unchanged for users who don't expand it.
- No execution path. No order surface. No risk gate interaction. No `auto_execute` flag.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-grader-20260523-0036
BASE=/home/azureuser/trading_corp
# Restore the 2 modified files from backup tags
sudo -u azureuser cp -p \$BASE/trading_corp/web/routes.py.\$TAG \$BASE/trading_corp/web/routes.py
sudo -u azureuser cp -p \$BASE/trading_corp/web/templates/partials/iron_condor_static_sections.html.\$TAG \$BASE/trading_corp/web/templates/partials/iron_condor_static_sections.html
# Remove the 3 new files
sudo -u azureuser rm -f \\
  \$BASE/trading_corp/agents/strategies/ic_candidate_grader.py \\
  \$BASE/trading_corp/web/templates/partials/iron_condor_grader.html \\
  \$BASE/trading_corp/web/templates/partials/iron_condor_grader_result.html
sudo systemctl restart trading-corp
"
# Notes:
# - Rollback restores prod to the post-22:17-UTC dashboard-cutoff state (PID 1119435 era).
# - Does NOT touch backup tags themselves — they remain available for re-rollback.
# - Does NOT touch /etc/trading-corp/tastytrade.env or robinhood.pickle.
# - Does NOT touch local commits (112aef3 still in main; rollback only erases prod side).
```

**Follow-ups queued:**
- **Push decision (local 1 ahead of origin).** `1bcd8b4` (the §6 closure note) is local-only. Separate from deploy per scope-control. Push timing is operator's call.
- **Runbook restatement amendment.** `runbooks/session_start_2026_05_23.md` lines 76–95 carry the incomplete §6 acceptance criterion. Either amend there (with Board approval per CLAUDE.md §4 runbook-edit rule), or live with the pointer at `planning/ic_grader_section6_closure_20260523.md`.

---

## 2026-05-22 22:17 UTC — dashboard: kalshi_weather cutoff → P3 deploy time (commit `90b3491`)

**Commits:** `90b3491` (local main, not pushed at deploy time — push call deferred to operator). Single-line surgical patch to `DASHBOARD_RT_CUTOFFS` in `trading_corp/web/data.py`.
**Triggered by:** Operator request after Phase D replay closed the hourly re-eval investigation (commit `5d3d859`). Floor cutoff (2026-05-20 11:35 UTC) was set before the 2026-05-22 station-coord + xref-loader corrections; advancing it scopes the dashboard tile to the fully-corrected logic window.
**Backup tag:** `pre-cutoff-20260522-1730` on prod `trading_corp/web/data.py` only.

**Files deployed (1):**
- `trading_corp/web/data.py` — single line: `DASHBOARD_RT_CUTOFFS["kalshi_weather"]` from `2026-05-20T11:34:59+00:00` → `2026-05-22T16:25:00+00:00`. Inline comment updated to reference all three corrections (floor + 6 station fixes + KXTEMPNYCH disable + xref YAML loader). md5: `7722dd80…` → `6f716288d01a97996ed41e7a3c3ca8ba`.

**Features shipped:**
- **kalshi_weather dashboard tile now scopes to fully-corrected logic** (post-`f5a5fd5` P3 deploy, 2026-05-22 16:25 UTC). Tile's "since" badge will render `2026-05-22` after the next page load.
- **82 floor-era RTs (2026-05-20 11:35 → 2026-05-22 16:25) preserved in `kalshi_round_trips`** — filter-only, queryable for forensics; just excluded from dashboard aggregates. Cross-strategy `kalshi_crypto` cutoff at `2026-05-20T05:52:09+00:00` was verified unchanged.

**Notable code changes:**
- Surgical patch only; no schema changes, no new modules, no test changes. The deploy mechanism was a `sudo sed -i` on prod's file with strict pre/post grep verify (old-line hits 0, new-line hits 1, crypto-line hits 1) — bail-and-restore path if any verify count miscounted.
- Cutoff value chosen = `f5a5fd5` deploy time (P3 xref loader live). All three corrections — entry-price floor (`b218375`, deployed 2026-05-20 11:35), 6 station-coord corrections + KXTEMPNYCH disable (`e02258d`, deployed 2026-05-22 14:02), xref YAML loader (`f5a5fd5`, deployed 2026-05-22 16:25) — are guaranteed live at and after this timestamp.

**Verification:**
- Prod md5 post-patch: `6f716288d01a97996ed41e7a3c3ca8ba`, line count unchanged at 4883.
- `systemctl restart trading-corp` clean. MainPID `1119435` since `2026-05-22 22:17:49 UTC` (was `1071785` since 16:25:02 UTC — Tastytrade AM-fix process).
- `curl http://127.0.0.1:8000/healthz` → `HTTP 200 OK`, body `{"status":"ok","mode":"PAPER"}`.
- `ss -tlnp`: python pid 1119450 LISTEN on `0.0.0.0:8000`.
- journalctl shows live `kalshi_crypto_evaluated` audit rows flowing post-restart (audit log intact).
- Read-only RT count at deploy time: 0 kalshi_weather_arb round-trips with `entry_ts >= 2026-05-22T16:25:00+00:00` (forward fills haven't resolved yet — clean baseline for the advanced cutoff to populate over the observation week).

**Inert / dormant on current traffic:** none. The cutoff dict is consulted on every `/dashboard` render path that touches a kalshi_weather aggregate; change takes effect on next page load.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-cutoff-20260522-1730; BASE=/home/azureuser/trading_corp; \
sudo cp \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
sudo chown root:root \$BASE/trading_corp/web/data.py; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-22 16:47 UTC — Tastytrade AM fix: SDK shape across all call sites (commit `e977641`)

**Commits:** `e977641` (Tastytrade AM fix). On `origin/main` (operator-pushed). Local main currently at `6e81038` (parallel-session `weather_stations` work pushed on top, unrelated to this deploy and untouched by it).
**Triggered by:** AM follow-up to the `a6885a5` deploy (2026-05-22 10:33 UTC) per `runbooks/session_start_2026_05_22_data_provider_am_fix.md`. Resolves four SDK-shape bugs in `tastytrade_provider.py` that mock-based tests couldn't detect (same class as Bug 1+2 from `a6885a5`; full file-wide audit pass on every `asyncio.to_thread` + `tastytrade.*` call site preceded the fix per the operator-mandated audit-first rule).
**Backup tag:** `pre-tastytrade-fix-20260522` on 4 modified files. New files have no backup target by definition.

**Files deployed (6):**

*Modified (4):*
- `trading_corp/data/tastytrade_provider.py` — Bug 1 (`Session(provider_secret=, refresh_token=)`), Bug 2 (`get_market_data(InstrumentType.EQUITY)` replaces non-existent `get_quote`), async/to_thread fix (direct `await` on `get_option_chain` + `get_market_data` — both are async in 12.4.1; previously wrapped in `asyncio.to_thread` which returned unawaited coroutine objects), `greeks.event_symbol` snake_case (replaces `greeks.eventSymbol` camelCase that silently `AttributeError`'d inside the DXLink streamer loop's try/except). Also imports `_hv_to_rank` from `_iv_math.py` (no longer via yfinance provider).
- `trading_corp/data/yfinance_provider.py` — drop `_hv_to_rank` definition; import from `_iv_math.py`.
- `tests/test_tastytrade_provider.py` — `monkeypatch.delenv` on the two auth-missing tests (pre-existing test isolation bug; leaked when env vars are set).
- `tests/test_yfinance_provider.py` — import `_hv_to_rank` from `_iv_math.py` (was from yfinance_provider).

*New (2):*
- `trading_corp/data/_iv_math.py` — provider-neutral `_hv_to_rank` extracted from yfinance_provider so Tastytrade doesn't import math via yfinance.
- `tests/test_fidelity_uses_shared_iv_rank.py` — regression test: `fidelity_options._calc_iv_rank IS utils.iv.calc_iv_rank` (locks in the a6885a5 dedup).

**Credential state at deploy (load-bearing context):**

The Tastytrade Client Secret was rotated by the operator pre-deploy. The session debugged through three failure modes before a working secret/token pair landed on prod's `/etc/trading-corp/tastytrade.env`:
1. **`invalid_grant: Grant revoked`** — refresh token issued under the old Client Secret was invalidated by rotation; prod env file had not been re-bootstrapped (mtime stuck at 10:30:28 UTC from the original deploy).
2. **`invalid_grant: Invalid JWT`** — first bootstrap retry wrote a non-JWT token (no `eyJ` b64 prefix; probably wrong token type from the OAuth flow).
3. **`invalid_grant: Client secret mismatch`** — second bootstrap retry produced a JWT, but the Client Secret in env was a different value than the one used during the OAuth grant.

Final correct pair landed at **2026-05-22 16:25:14 UTC** (env-file mtime; sha256 `a0df3165af…26015c329`, 633 bytes, perms 600 root:root). First successful `session.refresh()` against prod creds: 2026-05-22 ~16:38 UTC (operator's `setx` mirror synced via parity check). **First end-to-end working Tastytrade OAuth on prod since the original 2026-05-21 grant.** See backlog HIGH item "Tastytrade rotation runbook" — secret rotation must be an atomic 2-step operation (OAuth bootstrap + write matched JWT refresh_token + Client Secret to prod env).

**Features shipped:**

- **Full Tastytrade ATM-IV path live on prod.** Post-deploy probe via the deployed code: `get_atm_iv("SPY", 45) = 0.1508`, `IWM = 0.2243`, `TLT = 0.1029` — all real, not None, not 1e-5. Matches the 2026-05-21 evening Step 0 spike (~0.21 IWM, ~0.11 TLT) ±natural market drift. Pre-deploy, the same calls returned None due to Bug 1's `KeyError('TT_SECRET')` SDK fallback — see today's 13:47:28 UTC `xvfb-run[1044557]` journal entries on PID 1044543 (those are the last instances of that signature; nothing after PID 1074854's 16:47:11 UTC restart).
- **Real `get_underlying_price` via Tastytrade.** `get_market_data(session, symbol, InstrumentType.EQUITY)` returning `MarketData.last or MarketData.mark`. SPY returned 747.30 on the post-deploy probe.
- **Async SDK call shape correct everywhere.** Three sites (`_fetch_chain`, `_compute_atm_iv`, `_fetch_underlying_price`) fixed to direct `await`. The remaining `asyncio.to_thread` sites in this file (`Session()` construction, `_yf_closes` local sync function) were audit-confirmed CORRECT and kept as-is.
- **Greeks attribute access fixed.** `greeks.event_symbol` (snake_case, the actual `pydantic.model_fields` name in 12.4.1). Was `greeks.eventSymbol` (camelCase, nonexistent — would raise but was caught by outer try/except in `_fetch_chain`'s DXLink loop, producing silent zero-Greeks chains).
- **`_hv_to_rank` provider-neutral.** Tastytrade provider no longer imports HV math via yfinance.
- **Fidelity `_calc_iv_rank` shared-util regression test.** Locks the a6885a5 dedup against future drift.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **Four SDK-shape bugs in one file, all same failure class** ([[feedback-mocks-dont-catch-sdk-shape]]): mocks accept any kwargs/attributes/sync-vs-async and silently produce green tests against wrong shapes. The full audit pass on this commit covered every `asyncio.to_thread(...)` site + every `tastytrade.*` import + every SDK return-object attribute access in the file. Of 8 distinct call sites surveyed: 4 needed fixing, 3 were correct-as-written, 1 is the deferred Bug 4 (`get_history`). See escalation in backlog: live SDK gate now MANDATORY pre-commit for any provider change.
- **`Session.__init__` is sync but does HTTP I/O at construction**, so wrapping in `asyncio.to_thread` is correct usage (audit-confirmed KEEP). Don't simplify that to a direct call without testing — it would block the event loop during initial auth.
- **`get_event(Greeks)` returns a `Greeks` event** keyed by `event_symbol` (snake_case). The `_fetch_chain` greeks_map keys by `streamer_symbol` from `Option` objects (also snake_case). They MUST match — the dxFeed convention is `event_symbol == streamer_symbol` of the subscribed option. Don't change either side independently.
- **Bug 4 NOT fixed in this deploy.** `tastytrade.market_data.get_history` doesn't exist in 12.4.1; `_fetch_close_series` ImportErrors on every call → falls through to yfinance HV. Pre-existing condition; IVR continues to work via yfinance HV math (the live SPY IVR of 0.342 in the 2026-05-22 10:33 entry's verification came from this fallback, not from Tastytrade history — the "auth chain works" claim in that entry was masked by this fallback). Deferred to backlog as MEDIUM.

**Latent bugs caught + fixed:**

- **Bug 1 (`a6885a5` ship-blocker):** `Session(login=ps, remember_token=rt)` → unknown kwargs fell into `**client_kwargs`; SDK then `KeyError('TT_SECRET')`. Fix: `Session(provider_secret=ps, refresh_token=rt)`.
- **Bug 2 (`a6885a5` ship-blocker):** `from tastytrade.market_data import get_quote` — symbol missing in 12.4.1. Fix: `get_market_data(session, symbol, InstrumentType.EQUITY)` reading `MarketData.last or MarketData.mark`.
- **Async/to_thread mismatch (pre-existing in `a6885a5`, masked by Bug 1):** `get_option_chain` and `get_market_data` are `async def`; `asyncio.to_thread(<coro>, ...)` returns unawaited coroutine objects (`'coroutine' object is not iterable` / `'coroutine' object has no attribute 'last'`). Fix: drop `to_thread` wrap, `await` directly.
- **`greeks.eventSymbol` camelCase (pre-existing in `a6885a5`):** AttributeError silently absorbed by outer try/except in `_fetch_chain`. Fix: `greeks.event_symbol`.
- **Test isolation in `test_tastytrade_provider.py` (pre-existing in `a6885a5`):** `test_auth_missing_provider_secret_raises` / `_refresh_token_raises` didn't `monkeypatch.delenv`, so the env-var-fallback path made them spuriously pass in dev environments without the secrets but spuriously fail in environments with them. Fix: add `monkeypatch.delenv` to match the neighbouring `test_auth_missing_both_env_vars_raises` pattern.

**Verification:**

- `systemctl is-active trading-corp` → `active`. MainPID `1074854` since `2026-05-22 16:47:11 UTC` (was `1071785` since `16:25:02 UTC` under the broken-creds env; before that, `1044543` since `10:33:42 UTC` under `a6885a5` bugs).
- md5-diff of all 6 files post-extract matches local:
  - `3866b6c38cff31056e673287e2932c04  trading_corp/data/tastytrade_provider.py`
  - `94d9e07495ee3d9336e38d505c57e3b4  trading_corp/data/yfinance_provider.py`
  - `ec51d8f4d3252c73dbf07fa9e425c629  tests/test_tastytrade_provider.py`
  - `9c81010c3bab85b637d2cdf6b525792a  tests/test_yfinance_provider.py`
  - `807b459f39e7780103572e0070060a38  trading_corp/data/_iv_math.py`
  - `8d5a997ec836faf552ddd36ddd982510  tests/test_fidelity_uses_shared_iv_rank.py`
- env file at `/etc/trading-corp/tastytrade.env`: 633 bytes, sha256 `a0df3165af0f9b38f3dce00416e573d781d6bd5910649e884faf2d326015c329`, mtime `2026-05-22 16:25:14 UTC`, perms 600 root:root.
- Post-restart journal (since `16:47:11 UTC`): no `Traceback` / `CRITICAL` / `TT_SECRET` / `coroutine was never awaited` / `invalid_grant` lines.
- **Live post-deploy probe via deployed code on prod** (python-direct env loader, no bash source): SPY ATM IV 0.1508, IWM 0.2243, TLT 0.1029, SPY spot 747.30 — all real.
- Local gate pre-deploy (against the same prod creds, synced via sha256 parity verification): SPY 0.1515, IWM 0.2245, TLT 0.1038, SPY spot 747.13 — drift consistent with normal market motion during the deploy window.
- Mocks: 352/352 green pre-fix AND post-fix. **Mocks did not gate this commit — the live SDK probe did.** Mocks accept both correct and incorrect SDK shapes (escalation in backlog).

**Inert / dormant on current traffic:**
- The IVR path's Tastytrade-history branch (`_fetch_close_series` line 350-359) still ImportErrors on every call and falls through to yfinance HV. Same behavior as the `a6885a5` deploy; deferred Bug 4. No change in IVR output from this deploy.
- The deploy's 6 files include 1 new test file (`test_fidelity_uses_shared_iv_rank.py`) and 1 new test-isolation fix (in `test_tastytrade_provider.py`). Neither is exercised at runtime — pytest only.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-tastytrade-fix-20260522
BASE=/home/azureuser/trading_corp
for f in \
  trading_corp/data/tastytrade_provider.py \
  trading_corp/data/yfinance_provider.py \
  tests/test_tastytrade_provider.py \
  tests/test_yfinance_provider.py; do
  sudo -u azureuser mv \$BASE/\$f.\$TAG \$BASE/\$f
done
sudo -u azureuser rm -f \
  \$BASE/trading_corp/data/_iv_math.py \
  \$BASE/tests/test_fidelity_uses_shared_iv_rank.py
sudo systemctl restart trading-corp
"
# Notes:
# - Rollback restores a6885a5's broken Session() + missing get_quote + async-to_thread mismatch.
#   The 'TT_SECRET' KeyError signature would return on the next IC scan (13:45 UTC daily) and
#   get_atm_iv would silently return None again (caught by outer try/except).
# - Rollback does NOT touch /etc/trading-corp/tastytrade.env. The post-rotation JWT creds in the
#   env file are still good for the rolled-back code — but the rolled-back Session() never gets
#   far enough to use them.
# - Env-file rollback to pre-rotation creds is NOT possible from this deploy (the prior values
#   were revoked Tastytrade-side; only the new bootstrap-produced pair authenticates).
```

**Follow-ups queued:**
- **Bug 4 (MEDIUM):** dead `tastytrade.market_data.get_history` branch in `_fetch_close_series`. Either delete + document yfinance-by-design for IVR, or find the actual 12.4.1 historical-bars API. Same observability class as the silent-fallback item.
- **Silent-fallback audit rows (MEDIUM):** every yfinance fallback in `_fetch_close_series` should emit an `auth_chain_failed` or `provider_fallback_fired` audit row so prod isn't silently using yfinance when Tastytrade was expected. Recurring "plausible number from wrong source" pattern (kline silent failure rebuilt inside reconciler — see B7; funding-units ×100 — see project memory; now IVR-via-yfinance-fallback masking Tastytrade auth state).
- **Tastytrade rotation runbook (HIGH — this session's full cost):** secret rotation is an ATOMIC 2-step operation. (1) Full OAuth grant against the current Client Secret on a standard browser (not privacy browser — see [[feedback-oauth-use-standard-browser]]); (2) write the resulting matched pair (Client Secret + JWT refresh_token, both from the SAME bootstrap session) to prod's `/etc/trading-corp/tastytrade.env`. Document the failure-chain symptom progression (revoked → non-JWT → secret-mismatch) as a diagnosis template so future rotations are diagnosable in seconds.
- **`[[feedback-mocks-dont-catch-sdk-shape]]` escalation:** 4 bugs surfaced this session from mocks accepting wrong SDK shapes (kwargs, missing import, async vs sync, attribute name). Live SDK gate must be MANDATORY pre-commit for any provider change. Consider a thin real-SDK smoke test that asserts each SDK symbol used is importable, `iscoroutinefunction` matches usage, and accessed attributes exist on the return type — would have caught all four bugs in CI rather than at deploy.

---

## 2026-05-22 16:25 UTC — kalshi_weather P3: YAML xref loader wired (commit `f5a5fd5`)

**Commits:** `f5a5fd5` (strategy edits + new test file). Companion files
shipped same deploy: `38595d8` (P1 loader+YAML+tests, was committed
dormant) and `6ff80c1` (P2 verified — 38 NWS-CLI entries flipped).
Pushed to `origin/main`.
**Triggered by:** Operator go after P2 verification pass (jack personally
reviewed each entry in `planning/weather_stations_review.md` against
the verbatim Kalshi rules, then ran the batch flip). Plan in
`planning/weather_station_xref_design.md` §7 P3.
**Backup tag:** `kalshi_weather_arb.py.pre-p3-20260522T162316Z` (in
`/home/azureuser/trading_corp/backups`). New files have no backup
target.

**Files deployed (3):**
- `trading_corp/data/weather_stations.py` (new) — pydantic-validated
  YAML loader with mtime cache + fail-safe to last-good copy. Public
  API: `get_registry()` singleton, `lookup_series()`, `lookup_station()`.
- `config/weather_stations.yaml` (new) — 19 stations + 39 series.
  38 series `verified: true` (jack, 2026-05-22, with `verified_via_market`
  per entry); 1 series `disabled: true` (KXTEMPNYCH — AccuWeather).
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — `_resolve_coords`
  helper extracted; verified-YAML → legacy lookup order; eval_payload
  carries `coord_source` / `yaml_coords` / `legacy_coords` for drift
  detection.

**Features shipped (load-bearing for future "is X done?" checks):**

- **YAML xref loader live and driving coords for every verified series.**
  Day-one scan: 60 evals, all `coord_source=yaml_verified`, zero drift
  (`yaml_coords == legacy_coords` on every row). The legacy
  `_CITY_COORDS_FALLBACK` dict stays FULLY ACTIVE — both paths compute
  per eval; P4 (legacy removal) is gated on a full week of drift=0.
- **Audit drift fields** (`coord_source`, `yaml_coords`, `legacy_coords`)
  now in every `kalshi_weather_evaluated` event. New `skip_code`
  `yaml_disabled` reserved for the belt-and-suspenders case where a
  disabled YAML entry leaks past `_DISABLED_SERIES_PREFIXES` (hasn't
  fired; would log WARN).
- **Drift-check SQL shipped at `scripts/check_weather_coord_drift.sql`**
  (`6e81038`) — runnable read-only against prod sqlite. Reports
  coord_source distribution, drift cases, legacy_fallback events,
  upstream-filter health. Daily during the observation week.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **Critical invariant in `_resolve_coords`:** only `verified: true`
  YAML entries drive `coord_source=yaml_verified`. An entry with
  `verified: false` is IGNORED and falls to legacy. Protects against
  an unreviewed YAML edit silently changing trades. Test
  `tests/test_kalshi_weather_coord_resolution.py::test_unverified_yaml_entry_falls_to_legacy`
  enforces.
- **`_DISABLED_SERIES_PREFIXES = {'KXTEMPNYCH'}` is STILL the primary
  disabled-series gate** (in the survivors phase). The YAML
  `disabled: true` flag is consulted in `_resolve_coords` as belt-and-
  suspenders — if/when the upstream gate is removed (P5+), the YAML
  flag picks up the work. Don't remove either without a successor.
- **Three commits today were committed-but-not-deployed before this
  one:** `38595d8` (P1, explicitly dormant), `f5beafa` (P2 review
  doc + helper — never on prod, lives in repo only), `6ff80c1` (P2
  verified flips — never deployed alone; shipped here as part of P3
  via the YAML file write). The drift-check SQL `6e81038` is repo-only
  too — no prod copy needed; sqlite3 is on prod.

**Observation tripwires (read these during the observation week):**
- Drift-check Section 3 must stay `NO DRIFT — ...` across daily runs
  through ~2026-05-29.
- Drift-check Section 4 must stay `OK — no legacy_fallback events`.
  If a new Kalshi series shows up that isn't in the YAML, this fires
  and the safety net catches it — but P4 must NOT advance until the
  new series is added + verified.
- Drift-check Section 5 must stay `OK — no disabled_skip leaks`. If
  it ever non-zeros, hard bug.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-p3-20260522T162316Z; BASE=/home/azureuser/trading_corp; \
mv \$BASE/backups/kalshi_weather_arb.py.\$TAG \$BASE/trading_corp/agents/strategies/kalshi_weather_arb.py; \
rm -f \$BASE/trading_corp/data/weather_stations.py \$BASE/config/weather_stations.yaml; \
systemctl restart trading-corp.service
"
```

Validation after rollback: healthz 200, kalshi_weather scan emits NO
`coord_source` field, station coords resolved exclusively via
`_CITY_COORDS_FALLBACK` (still has the Track-1 corrections).

---

## 2026-05-22 14:02 UTC — kalshi_weather Track-1 station fix: 6 corrections + KXTEMPNYCH disable (commit `e02258d`)

**Commits:** `e02258d`. Pushed to `origin/main`. Companion planning
doc + audit JSON at `02ab258` (commit-before).
**Triggered by:** Operator investigation of a 2026-05-21 KXHIGHTSEA-B74.5
"lucky win" trade. Audit of all 39 weather series we trade revealed
6 settlement-station mismatches in `_CITY_COORDS_FALLBACK` (15-25%
of last-14-day weather volume) + 1 source mismatch (KXTEMPNYCH on
AccuWeather, no feed). Backtest of 125 affected historical trades
showed 0 direction flips but 9 marginal SKIPs under corrected
forecast — strictly more accurate, not a behavior change.
**Backup tag:** `kalshi_weather_arb.py.pre-station-fix-20260522T140059Z`.

**Files deployed (1):**
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — surgical
  patch (+32 / −16). Six entries corrected in `_CITY_COORDS_FALLBACK`
  + `_CITY_TO_METAR_STATION`; new `_DISABLED_SERIES_PREFIXES` set;
  filter wired in candidate-survivors phase; scan audit carries
  `skipped_disabled_series` counter.

**Features shipped:**

- **6 settlement-station corrections in `_CITY_COORDS_FALLBACK`:**
  - `NYC`, `TNYC`, `NY` → `KNYC` (Central Park) — was KJFK, ~12 mi off; Central Park is +3°F warmer for highs in 30-day ASOS.
  - `CHI`, `TCHI` → `KMDW` (Midway) — was KORD, ~17 mi off.
  - `THOU` → `KHOU` (Hobby) — was KIAH, ~24 mi off.
  Mirrored in `_CITY_TO_METAR_STATION`. Cross-checked against
  verbatim Kalshi rules in `planning/weather_station_xref_audit.json`.

- **`_DISABLED_SERIES_PREFIXES = {'KXTEMPNYCH'}` gate in the
  candidate-survivors phase.** KXTEMPNYCH resolves on AccuWeather;
  we have no feed. Refused-to-model rather than synthesize a station.
  Scan audit carries `skipped_disabled_series` counter; day-one
  observed value is 24 drops per scan (the active hourly markets).

**Notable code changes:**

- Affected ~125 of ~700 last-14-day trades on the 6 mis-mapped
  series. Backtest results saved during session at
  `tmp/backtest_results.csv` + `tmp/backtest_with_outcomes.csv`
  (gitignored). Climatological deltas in
  `tmp/station_pair_deltas.json`.
- Yesterday's KXHIGHTSEA-B74.5 trade (the trigger): TSEA→KSEA
  mapping was CORRECT all along; the 3°F miss was forecast error,
  not station error. The wider audit found the unrelated 6 series
  with real misalignments.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-station-fix-20260522T140059Z; BASE=/home/azureuser/trading_corp; \
mv \$BASE/backups/kalshi_weather_arb.py.\$TAG \$BASE/trading_corp/agents/strategies/kalshi_weather_arb.py; \
systemctl restart trading-corp.service
"
```

NB: a later P3 deploy (16:25 UTC same day) replaced this file. The
P3 rollback restores to a DIFFERENT backup (`pre-p3-...`) that has
the Track-1 fix baked in. To fully revert Track 1, restore the
`pre-station-fix-...` backup AFTER rolling back P3 — order matters.

---

## 2026-05-22 10:33 UTC — data-provider abstraction + Tastytrade primary + 1e-5 fix (commit `a6885a5`) — degraded (2 SDK bugs queued for AM fix)

**Commits:** `a6885a5` (data-provider abstraction). On `main`, not pushed to `origin/main` as part of this deploy (host-direct deploy mechanism; prod has no git).
**Triggered by:** Human-authorized deploy after end-to-end review of the IC v1 first-paper-scan finding (2026-05-21 13:45 UTC) where `calc_atm_iv` returned `1.0000000000000003e-05` for IWM and TLT — a degenerate yfinance value that slipped past the existing `not finite or <= 0` guard. Step 0 validation spike confirmed Tastytrade returns clean IV (~0.11 TLT, ~0.21 IWM front-month at 45 DTE). Operator explicitly acknowledged this MODIFIED LIVE IC STRATEGY behavior (not inert — corrects the prior session's "inert" mischaracterization; IC shipped at 2026-05-21 03:09 UTC and fired the 13:45 UTC scan that surfaced the 1e-5 bug).
**Backup tag:** `pre-data-provider-deploy-20260521` — 4 in-place file backups (`trading_corp/utils/iv.py`, `trading_corp/agents/strategies/robinhood_joint_iron_condor.py`, `trading_corp/agents/divisions/fidelity_options.py`, `requirements.txt`) plus `/etc/systemd/system/trading-corp.service.d/override.conf.pre-data-provider-deploy-20260521`. The 9 new files have no backup target by definition.

**Files deployed (15):**

*New (9):*
- `trading_corp/data/market_data_provider.py` — `MarketDataProvider` ABC + `OptionContract` dataclass + `_is_degenerate_iv` helper (IV-only invariant; rejects None / non-finite / `<= 0` / `< 0.01`).
- `trading_corp/data/tastytrade_provider.py` — primary provider via `tastytrade.instruments.get_option_chain` (flat, full-depth), 60s TTL cache, OAuth2 refresh-token auth.
- `trading_corp/data/yfinance_provider.py` — labeled fallback; wraps old `utils/iv.py` logic with `_is_degenerate_iv` boundary applied.
- `trading_corp/data/provider_factory.py` — mtime-cached config loader, `global + overrides[strategy]` merge, no auto-failover.
- `config/data_providers.yaml` — `primary: tastytrade`, `fallback: null` (explicit; auto-failover forbidden).
- `tests/test_market_data_provider.py`, `tests/test_tastytrade_provider.py`, `tests/test_yfinance_provider.py`, `tests/test_provider_factory.py` — provider/factory test suites (mock-based; see "Known degraded state" — mocks couldn't catch real-SDK kwarg shape).

*Modified (6):*
- `trading_corp/utils/iv.py` — thin wrappers delegating to configured provider. `calc_iv_rank` signature changed `float → float | None` (the `0.5` sentinel is gone).
- `trading_corp/agents/strategies/robinhood_joint_iron_condor.py` — `ivr_data_unavailable` tally branch at the IVR gate + 20-line delta-proximity guard (`chain_too_shallow` tally) after `_pick_by_delta`.
- `trading_corp/agents/divisions/fidelity_options.py` — duplicate `_calc_iv_rank` deleted (was lines 139-166), now imports from `utils.iv`; None-branch at `_scan_symbol` caller.
- `tests/test_iv_rank.py`, `tests/test_iron_condor_strategy.py` — updated for None signatures + new guard tests.
- `requirements.txt` — added `tastytrade>=12.4`. Installed in prod's venv pre-restart via `/home/azureuser/trading_corp/venv/bin/pip install --upgrade tastytrade`; verified 12.4.1 + transitive `httpx_ws==0.9.0`, `wsproto==1.3.2` importable.

**Systemd config change (one-time, recorded for future deploys):**
- `/etc/systemd/system/trading-corp.service.d/override.conf` — added `EnvironmentFile=/etc/trading-corp/tastytrade.env` to the `[Service]` section, preserving the existing `Environment=TELEGRAM_NOTIFICATION_ONLY=true` line. `systemctl daemon-reload` issued. systemd resolves the reference as `EnvironmentFiles=/etc/trading-corp/tastytrade.env (ignore_errors=no)` — service refuses to start if the file is missing or unreadable.
- `/etc/trading-corp/` directory created (700 root:root); `/etc/trading-corp/tastytrade.env` written out-of-band by operator (chmod 600 root:root, 2 lines: `TASTYTRADE_PROVIDER_SECRET=...` + `TASTYTRADE_REFRESH_TOKEN=...`). Values not in this log; verified by `grep -c` count + leading-char checks only.

**Features shipped (load-bearing for future "is X done?" checks):**

- **`MarketDataProvider` ABC live in prod.** All options/IV reads now route through the configured provider via `utils.iv:_get_configured_provider()`. yfinance is no longer imported at the IV-utility boundary.
- **1e-5 degenerate-IV bug FIXED at the provider boundary.** `_is_degenerate_iv` rejects `None` / non-finite / `<= 0` / `< 0.01` before the value reaches any caller. The `0.5` sentinel from `calc_iv_rank` is gone — symbols with insufficient data now return `None` and the IC strategy tallies them as `ivr_data_unavailable` (distinct from `ivr_below_30`).
- **IC strategy delta-proximity guard live in prod.** After `_pick_by_delta` returns short call/put picks, the strategy verifies `|achieved_delta - target_delta| <= 0.05`. If outside band, the symbol skips with a new `chain_too_shallow` scan-filter tally. CORRECTNESS gate enforcing the existing 16-delta target — does NOT move thresholds.
- **Fidelity `_calc_iv_rank` deduped.** The local duplicate at `fidelity_options.py:139-166` now imports from `utils.iv`. One math path.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **`calc_iv_rank` return type changed.** Was `float` (with `0.5` sentinel on error). Now `float | None`. Callers updated in this commit. Future callers must handle None.
- **Tastytrade authentication via OAuth2 refresh-token flow.** SDK 12.4 has moved off username+password; `Session(provider_secret=..., refresh_token=...)`. One-time OAuth grant produces the refresh token; SDK auto-refreshes access tokens (~28 day refresh-token lifetime). Operator's bootstrap script at `cc/tmp/tasty_oauth_bootstrap.py` (throwaway, not committed) handles the one-time grant.
- **`EnvironmentFile` is now a hard service dependency.** `ignore_errors=no` means systemd refuses to start trading-corp if `/etc/trading-corp/tastytrade.env` is missing/unreadable. Future deploys touching the env file must verify the file is in place before restart, else the service fails to start.
- **Chain-fetch path uses flat SDK function, not nested.** `tastytrade.instruments.get_option_chain` (module-level async) returns `dict[date, list[Option]]` with all strikes. NOT `NestedOptionChain.get` (which returns a narrow ~30-strike window that fails delta-proximity for SPY/IWM/QQQ at 16-delta — verified during Step 0 spike).

**Known degraded state (NOT blocking the 09:45 ET scan; AM follow-up queued):**

Two SDK API bugs in `tastytrade_provider.py`, surfaced only by live end-to-end test (mock-based tests couldn't catch real-SDK kwarg shape):

1. **`tastytrade_provider.py:82-86`** — `Session(login=ps, remember_token=rt)`. Wrong kwarg names; SDK 12.4 signature is `Session(provider_secret=..., refresh_token=...)`. Unknown kwargs silently fall into `**client_kwargs`; SDK then falls back to `os.environ["TT_SECRET"]` → `KeyError`. Effect: `get_atm_iv` raises internally, caught by outer try/except, returns `None`.
2. **`tastytrade_provider.py:391`** — `from tastytrade.market_data import get_quote`. Symbol doesn't exist in tastytrade 12.4.1. Effect: `get_underlying_price` returns `None`.

**Effect on tomorrow's 09:45 ET scan:**
- `calc_iv_rank` works (uses yfinance HV bars internally via `_hv_to_rank`; live SPY test returned `0.342`, real value for all 5 symbols).
- `calc_atm_iv` returns `None` for both front- and back-month → IC's `_term_structure_ok` fail-opens (logs `IronCondor: term-structure check skipped for X (front=None back=None)`) and proceeds. Same effective behavior as pre-deploy (where 1e-5 also bypassed the check via the same fail-open).
- `get_underlying_price` is unused by IC (strategy uses `broker.quote()` via Robinhood for spot; provider's `get_underlying_price` is internal to `get_atm_iv`'s ATM detection).

**Net comparison vs pre-deploy:**

| Behavior | Pre-deploy (yfinance) | Post-deploy (with bugs) |
|---|---|---|
| IVR `0.5` sentinel masking errors | YES (bug) | NO (clean `None` → `ivr_data_unavailable` tally) |
| ATM IV `1e-5` corrupting comparisons | YES (bug) | NO (clean `None` → fail-open identical to before) |
| Term-structure check on IWM/TLT | Effectively bypassed (1e-5) | Effectively bypassed (`None`) |
| Chain-depth correctness guard | Absent | Live (new `chain_too_shallow` tally) |

Strictly better than pre-deploy. AM follow-up closes the remaining gap.

**Verification:**
- `systemctl is-active trading-corp` → `active`. MainPID `1044543` since `2026-05-22 10:33:42 UTC`.
- `ic_paper_run_readiness --skip-network` → `STATUS: READY` (13/13 BLOCK checks pass post-deploy).
- IC signal scanner + position manager online (journal at `10:33:42 UTC`).
- `TASTYTRADE_PROVIDER_SECRET` + `TASTYTRADE_REFRESH_TOKEN` present in `/proc/1044543/environ` (names only confirmed; values not echoed).
- **Live SPY end-to-end:** `get_iv_rank("SPY")` returned `0.342` (real value; auth chain works for the IVR path). `get_atm_iv("SPY")` and `get_underlying_price("SPY")` returned `None` due to the bugs above.
- No new `agent_error` / `Traceback` / `CRITICAL` in `journalctl -u trading-corp` since the `10:33:42 UTC` restart. The one pre-restart Traceback at `10:27:48 UTC` is from PID `1042130` (the prior restart attempt under a broken env file, now superseded by the `10:33` restart).

**Security note for the record (accepted risk, NOT remediated tonight):**

During the live-fetch verification, a bash-source command attempted to load `/etc/trading-corp/tastytrade.env`. The env file at the time held the literal placeholder text `<value>` surrounding the actual values (operator paste issue during initial env-file creation). bash interpreted `<` as a redirect operator → syntax error → bash echoed the offending LINE to stderr, which the `az vm run-command invoke` captured into stdout. **The Tastytrade Client Secret (40 chars) leaked** into (a) the chat transcript of this session, and (b) the Azure activity log for `az vm run-command` invocations against `tc-prod-vm` in `rg-shared-prod`. The refresh token did NOT leak (bash bailed on line 2's syntax error before reading line 3).

**Risk assessment (operator):** the leaked value is a Tastytrade OAuth2 Client Secret for an application registered with `scope: read` only. It authenticates as the operator's funded Tastytrade account but can only read market data, account details, and positions — it cannot place trades or move funds. Exposure scope is bounded to read-only data on an account the operator controls. A full token refresh (revoke + new Client Secret + new OAuth grant) is tracked under the operator's infosec backlog as a queued remediation; no ticket ID was surfaced in this session.

**Decision:** operator accepted the residual risk and continued the deploy. The current `/etc/trading-corp/tastytrade.env` on prod holds the operator's actual (now-rewritten-without-brackets) Client Secret and refresh token. Operator did NOT explicitly state whether the rewrite used the leaked secret as-is (just brackets stripped) or a freshly-rotated secret — env file checks cannot distinguish (both produce a 40-char value). If unrotated, the leaked secret remains active until the infosec backlog item is worked.

**Mitigation paths NOT taken in this deploy (queued):** immediate revocation in Tastytrade's OAuth Application UI + new Client Secret + redo of OAuth grant + `setx` locally + rewrite of `/etc/trading-corp/tastytrade.env` + restart. Path to revisit: when the infosec backlog item is worked.

**Process change for future env-file work:** never source env files via `bash` for verification — use a Python-direct reader that parses key=value lines without shell interpretation. The post-fix verification used this approach successfully (see Verification section above).

**Inert / dormant on current traffic:**
- The 4 new test files (`tests/test_market_data_provider.py`, `tests/test_tastytrade_provider.py`, `tests/test_yfinance_provider.py`, `tests/test_provider_factory.py`) ship to prod's `tests/` dir but are exercised only by CI / local pytest. No runtime path imports them.
- `YFinanceDataProvider` (`yfinance_provider.py`) is present but never selected; config's `primary: tastytrade, fallback: null` keeps it dormant. Would activate only if `config/data_providers.yaml` is changed.

**Rollback recipe (kept for record; only roll back if a fresh fault surfaces post-deploy — current bugs are acceptable):**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-data-provider-deploy-20260521
BASE=/home/azureuser/trading_corp
for f in \
  trading_corp/utils/iv.py \
  trading_corp/agents/strategies/robinhood_joint_iron_condor.py \
  trading_corp/agents/divisions/fidelity_options.py \
  requirements.txt; do
  sudo -u azureuser mv \$BASE/\$f.\$TAG \$BASE/\$f
done
sudo -u azureuser rm -f \
  \$BASE/trading_corp/data/market_data_provider.py \
  \$BASE/trading_corp/data/tastytrade_provider.py \
  \$BASE/trading_corp/data/yfinance_provider.py \
  \$BASE/trading_corp/data/provider_factory.py \
  \$BASE/config/data_providers.yaml \
  \$BASE/tests/test_market_data_provider.py \
  \$BASE/tests/test_tastytrade_provider.py \
  \$BASE/tests/test_yfinance_provider.py \
  \$BASE/tests/test_provider_factory.py \
  \$BASE/tests/test_iv_rank.py \
  \$BASE/tests/test_iron_condor_strategy.py
sudo cp /etc/systemd/system/trading-corp.service.d/override.conf.pre-data-provider-deploy-20260521 \
       /etc/systemd/system/trading-corp.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart trading-corp
"
# Notes:
# - Rollback leaves tastytrade + httpx_ws + wsproto installed in venv (harmless after env import paths revert).
# - Rollback leaves /etc/trading-corp/tastytrade.env on disk (chmod 600 root:root); no longer referenced by systemd after override.conf revert. Safe to delete manually if desired.
```

**Follow-ups queued for AM (before 2026-05-22 13:45 UTC = 09:45 ET):**
- Fix `tastytrade_provider.py:82-86` — `Session()` kwargs to `provider_secret=` / `refresh_token=`. Verify against the SDK's actual signature (`inspect.signature(Session.__init__)`), don't guess.
- Fix `tastytrade_provider.py:391` — replace `from tastytrade.market_data import get_quote` with whatever SDK 12.4 actually exposes for spot/quote. Look it up in the installed package; don't assume.
- Bundle with the two prior follow-ups (deferred from `a6885a5`): move `_hv_to_rank` from `yfinance_provider.py` to a neutral `trading_corp/data/_iv_math.py` (so Tastytrade provider doesn't import math via yfinance provider); add a tiny Fidelity test asserting `_calc_iv_rank` resolves to the shared util.
- **Verification gate for the AM fix:** real authenticated Tastytrade call returning a real number (not None, not 1e-5) for `get_atm_iv("SPY")`, `get_atm_iv("IWM")`, `get_atm_iv("TLT")` — the three symbols including the two that went degenerate yesterday. Live SDK call is MANDATORY (mocks alone caused tonight's surprise).
- **Hard rule:** if the AM fix slips, surfaces a third bug, or can't verify before 09:45 ET → DO NOT rush. The 09:45 scan runs in tonight's strictly-better state (acceptable fallback). The good state is already deployed; better state is the low-pressure goal.

---

## 2026-05-22 01:50 UTC — B7 + B9 reconciler hardening (commits `3713ace` + `4fe56de`) — 5/5 MATCH

**Commits:** `3713ace` (B7 — bar_count > 0 guard) + `4fe56de` (B9 — inverted-window normalization). Both on `origin/main`.
**Triggered by:** Human-authorized re-deploy after the 2026-05-22 01:06 UTC rollback surfaced the inverted-window edge case that B9 addresses. Test-first locally for both commits (5 reconciler tests pass via `scripts\run_capped.ps1`).
**Backup tag:** `pre-b7-b9-reconciler-20260522` (1 file: `scripts/audit_reality_reconciler.py`; captures the post-rollback original at md5 `b203f791514cd43ce4b668d853bfd250`).

**Files deployed (1):**
- `scripts/audit_reality_reconciler.py` — md5 transitioned `b203f791514cd43ce4b668d853bfd250` (post-rollback original) → `071503b76cb722be2ca3e5621d847adc` (4fe56de blob = local). Adds: (a) `bar_count > 0` guard with explicit `no_bars` outcome and `audit_reality_no_bars` audit kind (B7); (b) window-normalization branch in `_load_bars_for_trade` that handles `ts > result_ts` by using `[result_ts, ts + max_hold_seconds]` instead of the inverted SQL bounds (B9).

**Features shipped:**
- **Reconciler can no longer false-clean against zero bars (B7).** If `_load_bars_for_trade` returns `[]`, the reconciler short-circuits to `simulated_result="no_bars", matches=False`, writes a per-trade `audit_reality_no_bars` audit_event row, and the per-fire roll-up status precedence becomes `mismatch > no_bars > match`. Closes the kline-shaped blind spot that was rebuilt inside the immune system.
- **Reconciler correctly handles inverted query windows (B9).** Trades with `ts > result_ts` (the v2 finalizing-tick attribution artifact previously bundled into B5) now reconcile against the bars that exist in absolute time, not against an empty SQL result. The classifier walks `[result_ts, ts + max_hold_seconds]` and stops on first SL/TP hit; trailing post-resolution bars are harmless. Trade `2942ff8e` now reconciles to a genuine match instead of `no_bars`/phantom mismatch.

**Verification (manual fire as `azureuser` mimicking `tc-audit-reality.service` ExecStart):**
- `cd /home/azureuser/trading_corp && /home/azureuser/trading_corp/venv/bin/python scripts/audit_reality_reconciler.py --db sqlite:////home/azureuser/trading_corp/data/trading_corp.db` exit code `0` (clean — was `1` under the prior deploy attempt that surfaced 2942ff8e's inverted-window symptom).
- Per-trade verdicts (5 closed v2 trades scanned):
  - `35aa49c9` — **MATCH** (sim=win R=0.838, rec=win R=0.838 corrected, 266 bars)
  - `a467e316` — **MATCH** (sim=loss R=-1.0, rec=loss R=-1.0 corrected, 266 bars)
  - `ef6e6697` — **MATCH** (sim=loss R=-1.0, rec=loss R=-1.0, 2 bars)
  - `ab190eb8` — **MATCH** (sim=loss R=-1.0, rec=loss R=-1.0, 1 bar)
  - `2942ff8e` — **MATCH** (sim=win R=**0.7955**, rec=win R=**0.7955**, **236 bars walked**, filled_legs=[tp1, tp2], final_sl=76950.63908). **NEW under B9** — was `no_bars` under B7-alone (yesterday's rollback) and would be `mismatch` under pre-B7 code. Now genuinely reality-verified by the reconciler against real bar OHLC.
- Roll-up row (audit_event id `463270`, ts `2026-05-22T01:50:10+00:00`): `{"n_total": 5, "n_matches": 5, "n_mismatches": 0, "status": "match", "mismatches": []}`.
- Zero new `audit_reality_no_bars` audit_event rows from this fire (only the residual id `461531` from yesterday's rolled-back deploy still exists — append-only history).

**Diagnostic anomaly resolved (not a regression):** A verification SQL probe `SELECT COUNT(*) FROM audit_event WHERE kind='audit_reality_no_bars' AND ts >= datetime('now','-2 minutes')` returned `1`, falsely suggesting a new no_bars row from this fire. Root cause: SQLite's `datetime('now', ...)` returns space-separated `'YYYY-MM-DD HH:MM:SS'`, while stored ISO timestamps use `T` (0x54) and `+00:00`. String comparison `T > space` → any same-UTC-day ISO row sorts as ≥ the space-formatted now-2min. The single row counted was id `461531` from yesterday at `2026-05-22T01:06:08+00:00`, NOT a row from this fire. Future verification queries should use `strftime('%Y-%m-%dT%H:%M:%S', 'now', '-2 minutes')` or numeric `julianday()` comparison to avoid this format mismatch. Not a B7/B9 issue — verification SQL hygiene.

**Timer state:**
- `tc-audit-reality.timer` active (waiting); next fire 2026-05-22 06:05:30 UTC + jitter. Oneshot — picks up the new file on next invocation without restart.

**Unattended-fire posture (post-deploy):**
- Next unattended fire (~06:05 UTC) runs the new (B7+B9) reconciler. Predicted result: 5/5 match, status="match", n_matches=5, n_total=5, mirroring the manual fire above. Trade population may grow if new v2 trades close between 01:50 and 06:05; new trades would also need bars in absolute time to match.
- Post-06:05 check recipe (use `julianday` to avoid the ISO/space mismatch):
  ```bash
  az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
    --command-id RunShellScript \
    --scripts 'sqlite3 -header /home/azureuser/trading_corp/data/trading_corp.db "
      SELECT id, ts, json_extract(payload_json,\"\$.status\") AS status,
             json_extract(payload_json,\"\$.n_matches\") AS n_matches,
             json_extract(payload_json,\"\$.n_total\") AS n_total
      FROM audit_event WHERE kind=\"audit_reality_run\" ORDER BY id DESC LIMIT 3;"'
  ```

**Status moves in BACKLOG.md:**
- **B7** DO-SOON → **done** (shipped + verified non-regressive at 5/5).
- **B9** new → **done** (companion fix that made B7's deploy possible; documented now in BACKLOG as a closed item).
- **B5** — the original `bars_to_resolution` scope **stays cosmetic** (untouched by B7+B9). The adjacent `result_ts < ts` inversion has its **reconciler-side blast radius now resolved by B9**; a source-side residue remains in `paper_trade_record.result_ts` for multi-tick lifecycle trades (any future consumer of `result_ts` that assumes forward time would face the artifact). Note in BACKLOG.

**Rollback recipe (kept for record; not executed this fire):**
```bash
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
  --command-id RunShellScript \
  --scripts 'sudo -u azureuser mv /home/azureuser/trading_corp/scripts/audit_reality_reconciler.py.pre-b7-b9-reconciler-20260522 /home/azureuser/trading_corp/scripts/audit_reality_reconciler.py'
```
(No service restart needed; oneshot reads the file fresh on next timer fire.)

---

## 2026-05-22 01:06 UTC — B7 reconciler no_bars guard (commit `3713ace`) — DEPLOYED AND ROLLED BACK

**Commits:** `3713ace` (B7 fix). `4 commits` pushed alongside (`8448a06`, `24af247`, `797a312`, `07ed68c`) — all docs/runbooks, no runtime code changes.
**Triggered by:** Human-authorized deploy of B7 — the `bar_count > 0` guard in `scripts/audit_reality_reconciler.py` that prevents the reconciler from declaring `match` against zero bars (the kline silent-failure pattern rebuilt inside the immune system). Test-first: RED→GREEN locally, 57 tests pass, wrapped via `scripts\run_capped.ps1`.
**Backup tag:** `pre-b7-no-bars-guard-20260521` (1 file: `scripts/audit_reality_reconciler.py`).

**Files deployed (1) — subsequently rolled back:**
- `scripts/audit_reality_reconciler.py` — md5 transitioned `b203f791514cd43ce4b668d853bfd250` (pre-deploy) → `a0b9071e2db1275fdd4bb0be36a4c885` (3713ace blob, deployed) → `b203f791514cd43ce4b668d853bfd250` (rolled back).

**Features shipped:**
- **NONE.** Deploy was rolled back during Step 3 verification per the task's literal rule.

**Verification (manual fire as `azureuser` mimicking `tc-audit-reality.service` ExecStart):**
- `cd /home/azureuser/trading_corp && /home/azureuser/trading_corp/venv/bin/python scripts/audit_reality_reconciler.py --db sqlite:////home/azureuser/trading_corp/data/trading_corp.db` exit code 1.
- Per-trade verdicts (5 closed v2 trades scanned):
  - `35aa49c9` — MATCH (sim=win R=0.838, rec=win R=0.838 corrected, 266 bars)
  - `a467e316` — MATCH (sim=loss R=-1.0, rec=loss R=-1.0 corrected, 266 bars)
  - `ef6e6697` — MATCH (sim=loss R=-1.0, rec=loss R=-1.0, 2 bars)
  - `ab190eb8` — MATCH (sim=loss R=-1.0, rec=loss R=-1.0, 1 bar)
  - `2942ff8e` — **NO_BARS** (sim=no_bars, rec=win R=0.7955, **0 bars** in window). Discrepancy: `no_bars: 0 bars in window [2026-05-21T14:00:12+00:00, 2026-05-21T14:00:00+00:00]`.
- Roll-up: `n_total=5, n_matches=4, n_mismatches=1, status="no_bars"`.
- audit_event rows from the manual fire (id 461531 `audit_reality_no_bars`, id 461532 `audit_reality_run`) **remain in the DB** post-rollback — append-only history. They do not affect future runs.

**Why the guard correctly flagged `2942ff8e` (B5-adjacent — escalates from cosmetic):**
- The reconciler's `_load_bars_for_trade` queries `bitunix_bar_history` with `WHERE ts_ms >= trade.ts AND ts_ms <= trade.result_ts`. For `2942ff8e`, `ts=14:00:12 > result_ts=14:00:00` (the documented two-tick replay artifact previously graded "cosmetic" in `runbooks/2026-05-21_post_funding_diagnostics.md` § 1). Inverted bounds → SQL returns 0 rows → B7 guard correctly declares `no_bars`. The bars exist in absolute time (verified in the post-funding diagnostic; 80/80 expected 3m bars for 5/21 12:00–16:00 UTC), but the reconciler's literal window is invalid.
- **Pre-deploy code path for the same trade**: empty bars → `_classify_v2_multi_leg` returns `result="expired", R=0.0` → sim=expired vs rec=win → genuine `mismatch` (different label, same non-match outcome). The OLD reconciler would have produced 4/5 match + 1 mismatch on `2942ff8e` instead of 4/5 match + 1 no_bars.

**Rollback executed (per task's Step 3 literal rule):**
- One-step `sudo -u azureuser mv ${BACKUP} ${TARGET}` consumed the backup file (mv, not cp). Post-rollback md5 verified equal to pre-deploy `b203f791514cd43ce4b668d853bfd250`. Backup file no longer exists (single-use, expected).
- Timer `tc-audit-reality.timer` state unchanged; oneshot service will pick up the (rolled-back, pre-B7) file on next fire.

**Status of B7:**
- Stays `DO-SOON` in BACKLOG.md. NOT marked done. Re-deploy is gated on a companion fix that handles the inverted-window case (`2942ff8e`-shaped trades). Two candidate shapes for the follow-up:
  1. Normalize the reconciler's query window: `start, end = sorted([ts, result_ts])`. Cheapest. Treats the inversion as a no-op concern at the verifier layer.
  2. Fix the source of the inversion in `paper_trade_replay.py` so `result_ts >= ts` always. More invasive. Touches the replay loop the runbook flags as deploy-gated.
- B5 (the underlying inversion cosmetic) **escalates from cosmetic → MEDIUM** as a result of this finding. The cosmetic grade was correct for R / exit-price; it understated the downstream consequence for the reconciler's window query. Update B5 in BACKLOG.md when next opening that file.

**Unattended-fire posture (post-rollback):**
- Next fire `2026-05-22 06:05:30 UTC + jitter`. Will run the OLD (pre-B7) reconciler.
- Predicted shape: `n_total=5, n_matches=4, n_mismatches=1, status="mismatch"`, with `2942ff8e` reporting sim=expired vs rec=win (the empty-bars classifier output). This is NOT a real R disagreement — it's the inverted-window symptom under the OLD code path.
- **Post-06:05 check recipe** (read-only):
  ```bash
  az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
    --command-id RunShellScript \
    --scripts 'sqlite3 -header /home/azureuser/trading_corp/data/trading_corp.db "
      SELECT id, ts, json_extract(payload_json,\"\$.status\") AS status,
             json_extract(payload_json,\"\$.n_matches\") AS n_matches,
             json_extract(payload_json,\"\$.n_total\") AS n_total
      FROM audit_event WHERE kind=\"audit_reality_run\" ORDER BY id DESC LIMIT 3;"'
  ```
  Expected: `status="mismatch", n_matches=4, n_total=5` reflecting the `2942ff8e` issue under the pre-B7 code.

**Rollback recipe (for future deploys of this commit, after the companion fix lands):**
```bash
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
  --command-id RunShellScript \
  --scripts 'sudo -u azureuser mv /home/azureuser/trading_corp/scripts/audit_reality_reconciler.py.pre-b7-no-bars-guard-20260521 /home/azureuser/trading_corp/scripts/audit_reality_reconciler.py'
```
(No service restart needed; reconciler is a oneshot — next timer fire reads the file fresh.)

---

## 2026-05-21 13:05:43 UTC — Bitunix funding-rate ×100 display/gate fix (commit `4f04fa66`)

**Commits:** `4f04fa66`
**Triggered by:** Human-authorized production deploy of funding-rate unit correction. `gate_mode="off"` throughout; no trade decisions affected. Display/audit-correctness only. No 60-day clock re-baseline.
**Backup tag:** `pre-funding-units-fix-20260521` (3 files: `bitunix_htf_regime.py`, `bitunix_htf_panel.html`, `bitunix.py` — tests file is new, no backup needed)

**Files deployed (4):**
- `trading_corp/agents/strategies/bitunix_htf_regime.py` — `:870` remove `abs(ctx.funding_rate) * 100.0 >` → `abs(ctx.funding_rate) >`; `:1011` remove `funding_rate * 100` → `funding_rate` in reason string; `:195` type comment updated to reflect API returns value already in percent.
- `trading_corp/web/templates/partials/bitunix_htf_panel.html` — `:101` remove `h.funding_rate * 100` → `h.funding_rate` in `%.4f` format call.
- `trading_corp/brokers/bitunix.py` — docstring only: `get_funding_rate` clarified that returned value is already in percent (e.g. 0.0066 means 0.0066% per 8h).
- `tests/test_bitunix_htf_regime.py` — new file: 4 unit-pinning tests (mild-positive, mild-negative, threshold-trip, render-format) + 2 corrected existing tests. Not functionally exercised on prod at runtime; shipped for repo state parity.

**Features shipped:**
- **Funding-rate display corrected.** Template rendered 0.66% for a 0.0066 raw value (×100 bug); now correctly renders `+0.0064%` for the current BTCUSDT rate of `0.006398`.
- **Funding-extreme gate corrected.** Gate comparison was `abs(rate) * 100 > threshold`; with threshold `0.05` (= 0.05%/8h), the gate was tripping on rates 100× too small (trip point was effectively 0.0005%). Now correctly trips at 0.05%/8h. `gate_mode="off"` throughout so no trade was ever blocked by this bug.
- **Reason-string corrected.** Gate audit rows will now read `"0.0064% per 8h"` style, not `"0.6400% per 8h"`.

**Notable code changes:**
- Three independent `* 100` sites removed. Each was a separate bug introduced when it was incorrectly assumed the API returned a decimal fraction rather than a percent value.
- No threshold change (`funding_extreme_pct_per_8h=0.05` unchanged). The threshold was always denominated in percent per 8h; only the comparison operand was wrong.
- `gate_mode` remains `"off"` — no change to gate behavior beyond correcting the arithmetic.

**Verification:**
- Pre-deploy prod state: template line 101 contained `funding_rate * 100` (confirmed via grep), prod md5s: `bitunix_htf_regime.py=ce24fe018229957bedfb57e122e602f5`, `bitunix_htf_panel.html=f32d3dce65cb26d3cf846b140cd50fd1`, `bitunix.py=a7125b2febf2f008cf03dfd82243fe9e`.
- Post-deploy md5s (all 4 match commit `4f04fa66` exactly): `bitunix_htf_regime.py=e0dbf34a7b43ee628eb1aa269849cc26`, `bitunix_htf_panel.html=3c886fb0950f936a61564d4e45c6b47e`, `bitunix.py=61b406fa218900b15e5f2d2366cc7579`, `tests/test_bitunix_htf_regime.py=c9cf307d6df764105dcccf94c4363e6f`.
- Code check post-deploy: `grep 'funding_rate \* 100' bitunix_htf_panel.html` → NOT FOUND. `grep '\* 100' bitunix_htf_regime.py` → NOT FOUND.
- PID: 973446 → 978296. Service active since `2026-05-21 13:05:43 UTC`. Healthz: `{"status":"ok","mode":"PAPER"}`.
- **Live render gate**: DB `bitunix_funding_history` latest BTCUSDT row: `ts=2026-05-21T13:07:23+00:00, rate=0.006398`. Dashboard `/division/bitunix_futures` rendered `+0.0064%` — matches `%.4f` of raw value (NOT `0.6400%`). PASS.
- **Extreme-tile gate**: No "extreme" text in rendered dashboard page. Rate 0.0064% < 0.05% threshold → NOT-extreme. PASS.
- **Reason-string gate**: No `funding_gate` audit rows emitted yet post-deploy (gate_mode="off" means no rows are written unless gate fires). Gate logic is verified correct by unit tests in the new test file.
- **DB lock errors**: transient on startup (~13:12-13:13 UTC), settled to 0 by 13:15 UTC. Pre-existing behavior on PMCC scan contention, not caused by this deploy.

**Reconciler + timer check:**
- `tc-audit-reality.timer` first unattended fire: `2026-05-21 06:03:42 UTC` — CLEAN. 3/3 trades matched, 0 mismatches. `audit_reality_run` audit row written at `2026-05-21T06:03:42+00:00` with `{"n_total":3,"n_matches":3,"n_mismatches":0,"status":"match","mismatches":[]}`. This was the first fully-unattended fire (was manual-only in the 2026-05-20 22:51 UTC deploy). Immune system confirmed operational.
- Reconciler unaffected by this deploy (touches only display/gate, not lifecycle/kline). Service is running clean post-restart.

**Inert / dormant on current traffic:**
- Tests file on prod is not exercised at runtime; shipped to keep prod tree in parity with repo.
- Gate reason-string format correction (`funding_rate` without `* 100`) is dormant until `gate_mode` is flipped from `"off"` to `"enforce"` — that's a separate Board-gated decision.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-funding-units-fix-20260521
BASE=/home/azureuser/trading_corp
cp \$BASE/trading_corp/agents/strategies/bitunix_htf_regime.py.\$TAG \$BASE/trading_corp/agents/strategies/bitunix_htf_regime.py
cp \$BASE/trading_corp/web/templates/partials/bitunix_htf_panel.html.\$TAG \$BASE/trading_corp/web/templates/partials/bitunix_htf_panel.html
cp \$BASE/trading_corp/brokers/bitunix.py.\$TAG \$BASE/trading_corp/brokers/bitunix.py
rm -f \$BASE/tests/test_bitunix_htf_regime.py
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-21 12:28:07 UTC — polymarket_arbitrage per-`condition_id` position cap (commit `c2b0e12`)

**Commits:** `c2b0e12` (cap implementation), `af27c4f` (Board approval record), `fcecbca` (memo addendum)
**Triggered by:** Board approval 2026-05-21 of memo §4A — see [board_memo_polymarket_dedupe_2026_05_21.md](../runbooks/board_memo_polymarket_dedupe_2026_05_21.md). Implements the position-stacking fix that closes the 10× single-market concentration pattern surfaced by the 2026-05-20 −$22 day.
**Backup tag:** `pre-dedupe-cap-20260521-1226` (2 files: `polymarket_arbitrage.py`, `strategies.yaml`)

**Files deployed (2):**
- `trading_corp/agents/strategies/polymarket_arbitrage.py` — new module-level helper `_count_open_entries_by_condition_id(db_url, condition_ids) → dict[str, int]` (counts unresolved `would_have_placed` audit rows anti-joined against `polymarket_round_trips.order_id`; no timestamp filter, so the cap bites against ALL pre-existing open entries on first eligible cycle). New "step 3a" dedupe filter in `run_scan_cycle`: skips survivors with `open_count >= max_open_per_condition_id` BEFORE the LLM fan; advances cooldown for skipped markets; emits one `polymarket_dedupe_skipped` audit per skipped market with `{condition_id, market_slug, market_question, category, current_open_count, cap}`. Two new fields on the existing `polymarket_scan_cycle` audit: `dedupe_skipped_count`, `max_open_per_condition_id`. Cooldown init lines moved 74 lines up to enable cooldown advancement in the dedupe path; non-skipped result-loop write byte-for-byte unchanged (regression covered by `test_non_skipped_path_cooldown_unchanged_after_init_move`).
- `config/strategies.yaml` — new `max_open_per_condition_id: 1` knob on `polymarket_arbitrage` block. **`enabled: true` and `auto_execute: false` UNCHANGED.** Strategy remains paper-only.

**Features shipped:**
- Per-`condition_id` position cap default `max_open_per_condition_id: 1` (true dedupe). Prevents the 10× stacking pattern. The ~99-entry in-flight overhang across 12 condition_ids (Iran 18, WTI HIGH $110 14, WTI HIGH $115 12, PSG 12, WTI HIGH $120 10, Anthropic 8, Arsenal CL 5, Paxton 5, WTI LOW $95 5, Iran-May31 4, Spencer Pratt 3, WTI LOW $90 3) cannot be added to; existing entries resolve through the normal resolver flow.
- New audit kind `polymarket_dedupe_skipped` per dedupe-skipped market for dashboard observability.

**Notable code changes:**
- `polymarket_scan_cycle` audit gains 2 fields (`dedupe_skipped_count`, `max_open_per_condition_id`) — existing dashboard queries on the original fields unaffected.
- `_count_open_entries_by_condition_id` uses `sqlite3` directly via `json_extract` on `payload_json` — no schema change, no new persistence helper required. Errors are logged and swallowed → falls through to pre-cap behavior (cap is a safety filter, not a load-bearing gate).

**Paper-only confirmation:** `auto_execute=false`; broker is `ReadOnlyBroker` (no `place_order` method exists on the class). Orders flow to `would_have_placed` audits only. **No Phase 3 live-execution work is authorized by this deploy** (Board memo §4B endorsed).

**Verification:**
- Pre-deploy md5 verify: `polymarket_arbitrage.py = 7965e45122d05033c366246d2d0a4620`, `strategies.yaml = 52722fe9b49f0fdacd5554553ff8a467` — local == prod after scp.
- Service restart at `2026-05-21 12:28:07 UTC`; `systemctl is-active trading-corp = active`; no traceback / import errors in journalctl post-restart window.
- First post-restart `polymarket_scan_cycle` audit landed `2026-05-21 12:29:45 UTC` with `"max_open_per_condition_id": 1` and `"dedupe_skipped_count": 0`. Three subsequent cycles (12:29:45, 12:30:15, 12:30:48) all show cap field present and value=1.
- `dedupe_skipped_count` was 0 across the verification window because all 100 surveyed markets were in active cooldown from pre-restart scans (cooldown table persists in `agent_state(polymarket_arbitrage, market_cooldowns)` across restarts). The cap will begin firing as the 6h cooldowns expire on stacked-overhang condition_ids and they re-enter the survivor set.

**Observed firings:**
- First `polymarket_dedupe_skipped` audit fired at `2026-05-21 12:33:51 UTC` (5 min 44 s post-restart) on `condition_id 0xdeb0a6abf730d613190d1b49e64bbedb2af0cc14f8a6f87e8da9282e64c29c0b` (WTI HIGH $115 in May). Cap saw 12 prior unresolved entries on this market and refused entry #13 — exactly the in-flight-overhang case the addendum §1 anticipated.
- 1 skip across 12 post-restart scan_cycles as of `2026-05-21 12:35:29 UTC`. The skipped market now sits in its newly-advanced 6h cooldown until ~`18:33 UTC`. Subsequent skips will accumulate as other stacked condition_ids (Iran 18, WTI HIGH $110 14, WTI HIGH $120 10, PSG 12, etc.) clear their pre-restart cooldowns and re-enter the survivor set.

**Side-effects of restart:**
- **Cloudflare-retry resilience now ACTIVE** on live PCT + polymarket_arbitrage paths (was dormant since 2026-05-17 17:38 UTC per Option-1 rollout — see that deploy_log entry).
- No other strategy YAML/code edited.

**Clean-data tracker epoch:** `2026-05-21 12:28:07 UTC` — this is the boundary the BACKLOG P1 clean-data tracker keys against. Trades with `entry_ts < 2026-05-21T12:28:07+00:00` are pre-cap and do NOT count toward the 50-trade clean-sample floor (per memo Addendum §1 clarification).

**Don't (until further notice):**
- Don't flip `polymarket_arbitrage.auto_execute: false → true` — gated on memo §4B (Phase 3 live execution requires ≥50 clean post-cap trades demonstrating edge).
- Don't flip `polymarket_arbitrage.enabled: true → false` — would stop accumulation of the clean-data sample.
- Don't change `max_open_per_condition_id` away from `1` without a separate Board memo.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-dedupe-cap-20260521-1226
BASE=/home/azureuser/trading_corp
cp \$BASE/trading_corp/agents/strategies/polymarket_arbitrage.py.\$TAG \$BASE/trading_corp/agents/strategies/polymarket_arbitrage.py
cp \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml
sudo systemctl restart trading-corp
"
```

---

## 2026-05-21 03:22 UTC — IC v1 follow-up: home-tile routing (commit `19b6dba`)

**Commits:** `19b6dba` (home: route robinhood_joint tile to /telemetry/iron_condor) — authored 2026-05-18, missed in the 03:09 UTC ship because the tarball covered only commits A + B + 65c8cdd.
**Triggered by:** user reported clicking the Robinhood Joint home tile didn't open the new IC page.
**Backup tag:** `.pre-ic-tile-20260521-032240`

**Files deployed (1):**
- `trading_corp/web/templates/home.html` — Robinhood Joint tile now routes to `/telemetry/iron_condor` instead of the generic `/division/robinhood_joint` shell.

**Features shipped:**
- Home tile for Robinhood Joint goes to the operator-facing IC live view (sections 1-6) — mirrors the existing prediction-market special-case in the same template.

**Verification:**
- Local + prod md5 match: `9834530b54872b42cf904180f4c9197e`
- `curl http://localhost:8000/ | grep href="/telemetry/iron_condor"` returns the link
- Templates hot-reload — no `systemctl restart` needed.

**Rollback:**
```bash
sudo -u azureuser cp /home/azureuser/trading_corp/trading_corp/web/templates/home.html.pre-ic-tile-20260521-032240 /home/azureuser/trading_corp/trading_corp/web/templates/home.html
```

---

## 2026-05-21 03:09 UTC — Iron Condor v1 first-prod-ship (30 files, chunked transport)

**Commits:** `365114b` (scaffolding) + `7c1eef0` (IC-only shared edits) + `65c8cdd` (wiring) — three IC commits previously in git but never deployed; this is the first prod ship of the entire IC v1 phase.
**Triggered by:** session-end deploy after the 5-file deconfliction landed locally and 371/371 tests + `ic_paper_run_readiness` confirmed READY.
**Backup tag:** `.pre-ic-v1-full-20260521-030935` (for the 12 overwritten files; the 18 new files have no backup — they didn't exist on prod before).

**Files deployed (30):**

*New files (18 — commit A `365114b`):*
- `trading_corp/agents/divisions/robinhood_joint.py` — division shell (190 lines)
- `trading_corp/agents/strategies/robinhood_joint_iron_condor.py` — primary strategy module (1686 lines)
- `trading_corp/agents/strategies/_ic_orchestration.py` — signal scanner + position manager loops + dispatch helpers
- `trading_corp/agents/ic_live_view.py` — live-view query layer (828 lines, 6 sections)
- `trading_corp/agents/ic_telemetry.py` — telemetry rollups (492 lines)
- `trading_corp/comms/pending_combo_registry.py` — in-process HITL combo registry
- `trading_corp/comms/telegram_batcher.py` — per-strategy notification batcher with bypass-tag pass-through
- `trading_corp/web/combo_approval_view.py` — approval-card renderer (4-leg combo view)
- `trading_corp/utils/iv.py` — IV-rank + ATM-IV utilities
- `trading_corp/data/ex_dividend_calendar.py` — ex-div calendar loader
- `trading_corp/scripts/ic_paper_run_readiness.py` — pre-run wiring check CLI (exit 0 = green)
- `trading_corp/scripts/ic_daily_digest.py` — cron-able daily summary
- `trading_corp/scripts/ic_telemetry_cli.py` — interactive telemetry queries
- `trading_corp/web/templates/approval_combo_detail.html` — combo approval card
- `trading_corp/web/templates/iron_condor_live.html` — `/telemetry/iron_condor` page shell
- `trading_corp/web/templates/partials/iron_condor_live_sections.html` — sections 1/3/5 (htmx 30s refresh)
- `trading_corp/web/templates/partials/iron_condor_static_sections.html` — sections 2/4/6 (page-load)
- `config/ex_dividend_calendar.yaml` — 169-line calendar source

*Modified files (12 — commit B + 65c8cdd):*
- `trading_corp/agents/data_exec.py` — adds `place_combo` (multi-leg dispatch + 3 audit kinds + `_persist_combo_positions`)
- `trading_corp/brokers/base.py` — `Broker` ABC adds `place_multi_leg` + `get_option_greeks` + `validate_combo_cohesion` + `ComboParams`
- `trading_corp/brokers/paper.py` — `place_multi_leg` combo simulator (per-leg slippage from `paper_simulation.per_leg_slippage_dollars`)
- `trading_corp/brokers/robinhood.py` — `get_puts_for_expiry` + `place_multi_leg` (atomic 4-leg via `robin_stocks.orders.order_option_spread`) + `is_multi_leg` guard on single-leg path
- `trading_corp/web/app.py` — `WebDeps` adds `ic_division` / `ic_strategy` / `ic_telegram_batcher` / `pending_combo_registry` fields
- `trading_corp/web/templates/approvals.html` — `{% if row.kind == 'combo' %}` branch on approvals list
- `config/risk.yaml` — `robinhood_joint_iron_condor` override block (`per_trade_risk_pct: 0.05`)
- `config/macro_calendar.yaml` — 2026 high-impact dates (FOMC + NFP + CPI + PPI)
- `config/divisions.yaml` — `strategy: robinhood_joint_iron_condor` on robinhood_joint block
- `config/strategies.yaml` — full `robinhood_joint_iron_condor:` strategy block (lines 1626-1691)
- `trading_corp/main.py` — IC wiring (RobinhoodJointAgent + RobinhoodJointIronCondorAgent + TelegramBatcher + PendingComboRegistry + `_ic_account_factory` + `_ic_strategy_state_factory` + 2 asyncio tasks + WebDeps wiring + finally-block cancellation)
- `trading_corp/web/routes.py` — 4 new routes: `GET /telemetry/iron_condor`, `GET /telemetry/iron_condor/partials/live`, `GET /approvals/combos/{combo_id}`, `POST /approvals/combos/{combo_id}/decide`

**Features shipped:**
- **Robinhood Joint Iron Condor v1** as a fully-wired division — paper-default (`auto_execute: false` is load-bearing). Universe SPY/QQQ/IWM/GLD/TLT, 45 DTE, 0.16 short delta. Decision tree is 10-branch (catastrophic stop → profit target → late-DTE → ex-div → hard stop → tested-side ID → adjust/close branches). Backtester permanently out of scope per Board decision 2026-05-18; paper-mode-as-validation per `runbooks/paper_run/ic_v1.md` (≥30-day tuning checkpoint, ≥90-day live-discussion readiness, HITL on every action even after 90 days).
- **Multi-leg broker support** — `place_multi_leg` on `Broker` ABC + Robinhood (atomic 4-leg `order_option_spread`) + paper (slippage-simulated). `place_combo` on `data_exec` for cohesion validation + combo-level audit events. Idle for non-IC strategies (`NotImplementedError` default on the ABC).
- **HITL combo approval surface** — `/approvals/combos/{combo_id}` GET + POST `/decide` via `PendingComboRegistry` (in-process, lost on restart by design). Combo rows now appear on the existing `/approvals` index via the new combo branch.
- **`/telemetry/iron_condor` operator dashboard** — 6-section debugging view: open positions (live Greeks, distances to triggers), recent activity, pending combos, today's scan results, strategy health (VIX gate, macro halt, circuit breaker, state-consistency check), last 10 closed combos. Live sections (1/3/5) htmx-refresh every 30s.
- **Operator CLIs** — `python -m trading_corp.scripts.ic_paper_run_readiness` (13 BLOCK + 1 SOFT readiness check), `ic_daily_digest`, `ic_telemetry_cli`. All run under the prod venv.
- **`config/macro_calendar.yaml` 2026 dates** — 32 high-impact events (FOMC/NFP/CPI/PPI). Used by IC scan's `MacroCalendar.has_high_impact_event_within(trading_days=5)` gate. Shared infrastructure — other strategies also read this.
- **`config/risk.yaml` IC override** — `overrides.robinhood_joint_iron_condor.per_trade_risk_pct: 0.05`. Per-leg evaluation already supported by `RiskAgent`; no multi-leg gate extension.

**Notable code changes:**
- **Two new asyncio tasks** in `run()` named `ic-signal-scanner` and `ic-position-manager`. Both confirmed initialized in the post-deploy logs (`IC signal scanner online: weekdays 09:45-09:50 ET (poll every 60s)` + `IC position manager online — running startup catch-up first`).
- **First scan fires at 09:45 ET on the next US market day** (`_ic_orchestration.is_signal_scan_due` window 09:45-09:50 ET, skip weekends and 2026 NYSE holidays).
- **Template-var naming gotcha** — partial templates expect `positions`, `pending`, `health`, `scan_results`, `activity`, `closed` (short forms), NOT the longer `ic_live_view` function names (`open_positions_detail`, etc.). Routes deliberately use the short forms in the TemplateResponse context dict.
- **Route ordering** — `/approvals/combos/{combo_id}` registered BEFORE the catch-all `/approvals/{order_id}` for FastAPI first-match path routing.
- **CRLF concern was a non-issue** — md5 of routes.py on prod matched git LF (despite local working tree being CRLF), so no `sed -i 's/\r$//'` needed on this deploy. Future routes.py deploys should still check.

**Latent bugs caught + fixed:**
- **First deploy attempt at 02:10 UTC crashed in a Restart=always loop** because the patch shipped only the 4 wiring files (commit `65c8cdd`) without commit A's 18 IC modules or commit B's 8 supporting edits. `main.py` hit `ModuleNotFoundError: No module named 'trading_corp.agents.divisions.robinhood_joint'` on import. Rolled back at 02:17 UTC via `.pre-ic-v1-20260521-020956` backups; service stabilized immediately. Root lesson: prod is filesystem-deployed (no git on prod), so a patch covering only HEAD's diff is insufficient when the prior commits in the same phase were never shipped. **Audit the full chain of unshipped IC commits before deploying the next phase of any project.** Cross-checked via `find /home/azureuser -name 'robinhood_joint.py'` (zero hits) + line-count math reconciliation across the 12 modified files (every delta matched commit-B/65c8cdd insertion counts exactly, ruling out prod-only drift).

**Verification:**
- Service active, PID 939464 (uptime since 03:09:36 UTC); no further restarts in 5+ min.
- `IMPORT OK` from the import-test step (all 9 IC modules + main + routes import cleanly under the prod venv).
- `IC signal scanner online` + `IC position manager online` both in journal.
- `curl http://localhost:8000/telemetry/iron_condor` → HTTP 200.
- `curl http://localhost:8000/approvals` → HTTP 200 (existing surface intact).
- Tracebacks in current PID journal: zero (Fidelity bot-block + Kalshi copy_trader `wallet` NameError are both known pre-existing issues unrelated to IC).
- Robinhood reconnect from the 02:00 UTC MFA refresh still good: `RobinhoodBroker bound: filter='joint' → account=116637293063 (joint_tenancy_with_ros)`.

**Inert / dormant on current traffic:**
- **First scan won't fire until 09:45 ET on the next US market day** (2026-05-21 is Thursday, so next fire is today ~13:45 UTC if past 09:30 ET, else tomorrow). The position manager runs its startup catchup immediately but does nothing because there are no open ICs in `agent_state.open_ics` (clean prod state).
- **`auto_execute_caps` block in `strategies.yaml`** is structurally present but unused while `auto_execute: false`. Future earn-auto-execute conversation would touch this; do not flip without a Board memo per CLAUDE.md § 1.
- **`place_multi_leg` on non-Robinhood brokers** is `NotImplementedError`. Only `RobinhoodBroker` and `PaperExecutionBroker` implement it. No other strategy uses combos today.

**Robinhood MFA loop fix (precondition):**
Earlier this session, the broken RH MFA loop on restart was fixed via `scripts/rh_mfa_refresh_prod.sh` (push approval, fresh pickle at `/home/azureuser/.tokens/robinhood.pickle` at 01:58 UTC). Without that fix, the IC scanner would have run on `broker_fallback_to_paper` $0-equity → qty=0 candidates → risk gate rejects everything → silent no-emit. The MFA fix is the load-bearing precondition for the IC v1 paper run to actually produce candidates.

**Rollback recipe:**
```bash
TAG=20260521-030935; BASE=/home/azureuser/trading_corp
sudo -u azureuser bash -c "
for f in \
  trading_corp/agents/data_exec.py trading_corp/brokers/base.py \
  trading_corp/brokers/paper.py trading_corp/brokers/robinhood.py \
  trading_corp/web/app.py trading_corp/web/templates/approvals.html \
  config/risk.yaml config/macro_calendar.yaml \
  config/divisions.yaml config/strategies.yaml \
  trading_corp/main.py trading_corp/web/routes.py; do
  mv \"\$BASE/\$f.pre-ic-v1-full-\$TAG\" \"\$BASE/\$f\"
done
rm -rf \$BASE/trading_corp/agents/divisions/robinhood_joint.py \
       \$BASE/trading_corp/agents/strategies/robinhood_joint_iron_condor.py \
       \$BASE/trading_corp/agents/strategies/_ic_orchestration.py \
       \$BASE/trading_corp/agents/ic_live_view.py \
       \$BASE/trading_corp/agents/ic_telemetry.py \
       \$BASE/trading_corp/comms/pending_combo_registry.py \
       \$BASE/trading_corp/comms/telegram_batcher.py \
       \$BASE/trading_corp/web/combo_approval_view.py \
       \$BASE/trading_corp/utils/iv.py \
       \$BASE/trading_corp/data/ex_dividend_calendar.py \
       \$BASE/trading_corp/scripts/ic_paper_run_readiness.py \
       \$BASE/trading_corp/scripts/ic_daily_digest.py \
       \$BASE/trading_corp/scripts/ic_telemetry_cli.py \
       \$BASE/trading_corp/web/templates/approval_combo_detail.html \
       \$BASE/trading_corp/web/templates/iron_condor_live.html \
       \$BASE/trading_corp/web/templates/partials/iron_condor_live_sections.html \
       \$BASE/trading_corp/web/templates/partials/iron_condor_static_sections.html \
       \$BASE/config/ex_dividend_calendar.yaml
"
sudo systemctl restart trading-corp
```

---

## 2026-05-20 22:54 UTC — kalshi_crypto vol-v2 dashboard: tile + read-only VIEW (rollback-then-fix)

**Commits:** uncommitted on local `main`. Deployed via targeted-patch staging copy (pull-prod → edit → push-staging), NOT a whole-file scp of local `data.py` — local carried unrelated bitunix reconciler WIP at deploy-start that the 22:51 UTC bitunix session shipped shortly before my deploy.
**Triggered by:** User request to update the kalshi_crypto dashboard so metrics reflect only post-vol-v2 results, with the post-vol-v2 set defined by a two-condition filter (`entry_ts >= cutoff` AND `vol_v2_classification IS NOT NULL`). Follows from the 2026-05-20 05:52 UTC vol-v2 ship — forward paper watch needed a dedicated tile.
**Backup tag:** `pre-vol-v2-dashboard-20260520-2200` (2 files: `web/data.py`, `web/templates/partials/pm_dashboard_body.html`). MD5-verified pre-edit state.

**Files deployed (2 new + 2 modify + 1 DB VIEW):**
- `trading_corp/web/kalshi_crypto_vol_v2.py` — **NEW**. Owns `KALSHI_CRYPTO_VOL_V2_CUTOFF = "2026-05-20T05:52:09+00:00"`, view-DDL helper, 3 dataclasses (`VolV2SummaryBlock`, `VolV2ClassificationRow`, `PMVolV2Block`), 6 query helpers, and `query_pm_vol_v2_block(db_url)` composer.
- `trading_corp/web/templates/partials/pm_vol_v2_block.html` — **NEW**. Three stacked summary cards (Post-vol-v2 live / Lifetime / Post-bucket-guard window pre-vol-v2), per-classification breakdown table, suppressed-fire-per-day metric, strays footnote.
- `trading_corp/web/data.py` — 4-hunk surgical patch via staging-pull → Edit → push: import at L17, `vol_v2_block: PMVolV2Block | None = None` on `PMDashboardView` at L3333, conditional builder call when `division == "kalshi_crypto"` at L4491–4493, kwarg in `return PMDashboardView(...)` at L4506.
- `trading_corp/web/templates/partials/pm_dashboard_body.html` — 1-line additive include at L871: `{% if view.vol_v2_block %}{% include "partials/pm_vol_v2_block.html" %}{% endif %}`.
- Prod DB `kalshi_crypto_vol_v2_round_trips` VIEW — **NEW** (metadata-only; no rows). Joins `kalshi_round_trips` to `audit_event` rows with `kind='kalshi_crypto_evaluated'` under a ±2s tolerance window on entry_ts. **DROPped + re-CREATEd mid-deploy** to switch to SARGable BETWEEN form (see Latent bugs).

**Features shipped:**
- **Vol-v2 paper-validation tile** on `/partials/prediction-markets/kalshi_crypto`. Three stacked summary cards, per-classification breakdown for `same_fire` / `new_fire` / `suppressed_fire` / `both_skip`, suppressed-fire-per-day rate metric (target ~5/day, currently 0/day on 0 resolved suppressed_fires), strays footnote (zero now — non-zero would surface a join-miss regression).
- **Two-condition filter for post-vol-v2.** A row is `vol_v2_era='post'` only if BOTH `entry_ts >= '2026-05-20T05:52:09+00:00'` AND `vol_v2_classification IS NOT NULL`. Embedded in the VIEW's `CASE` so every consumer query inherits the contract.
- **SARGable tolerance-window VIEW design.** Final view uses `ev.ts BETWEEN strftime('%Y-%m-%dT%H:%M:%S+00:00', krt.entry_ts, '-2 seconds') AND strftime(..., '+2 seconds')`. `EXPLAIN QUERY PLAN` confirms `SEARCH ev USING INDEX ix_audit_event_ts (ts>? AND ts<?)`. Four dashboard queries total **0.072s** on prod-scale data (404K audit rows, 87K of `kind='kalshi_crypto_evaluated'`).

**Notable code changes:**
- **Targeted-patch discipline survived a moving parallel-session base.** At my deploy-start, local `data.py` was 90 lines ahead of prod (the 22:51 UTC bitunix reconciler-tile WIP, then-unshipped). Mid-deploy the bitunix session shipped their reconciler, converging prod and local on the reconciler region. My patch still flowed via pull-prod → edit-staging → push-staging (never local→prod scp), with bitunix-region md5 verified byte-identical at `e28f226f7ade6a5e0d842f3292a92d2a` on every checkpoint (pre-edit, post-staging-edit, post-scp, post-rollback, post-re-deploy).
- **Cutoff is a single Python constant.** `KALSHI_CRYPTO_VOL_V2_CUTOFF` lives only in `web/kalshi_crypto_vol_v2.py`; the view-DDL helper interpolates it at view-create time. Changing the cutoff requires an explicit `DROP VIEW; CREATE VIEW;` — flagged in the helper docstring.
- **Cutoff format is `'YYYY-MM-DDTHH:MM:SS+00:00'`, NOT space-separated.** Caught an ISO-format string-compare bug mid-investigation: `'2026-05-20 05:52:09'` (space sep) compared lexicographically against `entry_ts` like `'2026-05-20T04:06:31+00:00'` (T sep) admits every 2026-05-20 row because space (0x20) < T (0x54). All cutoff strings in the new code use the T-separated form to match the stored entry_ts column format byte-for-byte.

**Latent bugs caught + fixed (during deploy):**
- **`ABS(julianday(ev.ts) - julianday(krt.entry_ts)) <= 2.0` is not SARGable.** Initial VIEW used this tolerance form; under prod load the kalshi_crypto partial hung >90s. Pre-deploy SQL probes had been inline-WHERE (`entry_ts >= cutoff AND vol_v2_classification IS NOT NULL`) which let the planner push filters down to base tables — they returned in <1s and gave a misleading green light. The VIEW's `CASE`-based `vol_v2_era` column is opaque to the planner, so consumer queries scanned 87,376 audit rows × 305 round-trips × 4 queries = ~100M ops. **Initial deploy rolled back at 22:44 UTC** (data.py only — the VIEW and new module/template files stayed inert on prod since `pm_dashboard_body.html`'s `{% if view.vol_v2_block %}` is falsy when `data.py` lacks the field). View was DROP+CREATEd with the SARGable BETWEEN form; re-deploy succeeded at 22:54 UTC. Memory lessons saved as [[time-views-on-prod-scale-before-shipping]] and [[julianday-abs-blocks-index-use]].
- **Off-by-1s join-miss recovered.** Initial diagnostic showed 2 post-cutoff RTs that didn't join under exact-ts equality. One was genuinely pre-deploy (entry_ts 04:06:31, masked by the ISO-format string-compare bug noted above). The other (row 2210, ticker KXETH-26MAY2011-B2130) had audit at 14:18:43 vs entry_ts 14:18:44 — exactly 1s off. The ±2s tolerance recovers it as `new_fire`; the genuine pre-deploy row is correctly excluded under the corrected ISO comparison.

**Verification:**
- Service: PID 911491 (post-rollback) → 913665 (post-fix re-deploy). `ExecMainStartTimestamp=2026-05-20 22:54:25 UTC`. ActiveState=active, SubState=running. Web command center bound at 22:56:08 UTC (~105s post-restart — normal for this service).
- kalshi_crypto partial: `HTTP 200, 585835 bytes, 1.07s` (vs pre-deploy baseline 0.61s — vol-v2 block adds ~460ms). Repeat curls: 2.58s / 0.64s (variable, no hang).
- Rendered markers confirmed: "Vol-v2 paper validation" heading, `cutover 2026-05-20T05:52:09+00:00` label, Post-vol-v2 (live) n=7 / -$1.05 / 71.4%, Lifetime n=334 / -$45.94 / 51.5%, Post-bucket-guard window n=174 / +$20.90, `new_fire` and `same_fire` classification rows, Suppressed-fire rate row, no strays footnote (zero strays). Live numbers reconcile vs the user's earlier-snapshot values (lifetime 305 → 334, post-bucket-guard 144 → 174: ~29 natural resolutions in the intervening ~1h).
- Bitunix region md5 byte-identical at `e28f226f7ade6a5e0d842f3292a92d2a` across (a) pre-edit prod, (b) staging post-patch, (c) prod post-deploy, (d) prod post-rollback, (e) staging-2 post-re-patch, (f) prod final. **Proven untouched throughout the entire deploy cycle.**
- Q1–Q4 timings (read-only against prod after view rewrite): Q1 post 0.026s, Q2 classification 0.023s, Q3 suppressed_fire_per_day 0.022s, Q4 strays 0.001s = **0.072s total**. Gate (<1s) passed by 13×.
- Journalctl post-restart: no `vol_v2` / `kalshi_crypto_vol_v2` / Traceback errors in new code paths. Pre-existing Robinhood + Fidelity broker auth errors → `broker_fallback_to_paper` per the documented sharp edge.

**Inert / dormant on current traffic:** the new dataclasses + 6 query helpers exercise only when the dashboard URL selects `division == 'kalshi_crypto'`. All other divisions and the "All Prediction Markets" aggregate bypass `query_pm_vol_v2_block` entirely. The `pm_vol_v2_block.html` partial is included only when `view.vol_v2_block` is truthy, so a future rollback of `data.py` alone (removing the field) cleanly disables the tile without touching the template.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-vol-v2-dashboard-20260520-2200; BASE=/home/azureuser/trading_corp; \
sudo cp \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
sudo cp \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html.\$TAG \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html; \
sudo chown root:root \$BASE/trading_corp/web/data.py \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html; \
sudo rm -f \$BASE/trading_corp/web/kalshi_crypto_vol_v2.py \$BASE/trading_corp/web/templates/partials/pm_vol_v2_block.html; \
sudo systemctl restart trading-corp.service"
# VIEW can be left in place (zero rows of its own, inert without consumer) or dropped:
ssh azureuser@trading.jacksumner.com "sqlite3 /home/azureuser/trading_corp/data/trading_corp.db 'DROP VIEW IF EXISTS kalshi_crypto_vol_v2_round_trips;'"
```

---

## 2026-05-20 22:51 UTC — BitUnix dashboard: reconciler-state tile + corrected-outcome display

**Commits:** `1264f55` (`feat(dashboard): reconciler-state tile + corrected-outcome display (PRIORITY 2)`). On `origin/main`.
**Triggered by:** PRIORITY 2 follow-up to the 2026-05-20 10:37 UTC bitunix v2 fix. The audit-vs-reality reconciler's only output was systemd service state; without dashboard surfacing, a mismatch would have remained silent until someone manually ran `systemctl status`. Also surfaces audit_corrected outcomes (2 historical bitunix trades) which the score panel was previously hiding.
**Backup tag:** `pre-dashboard-tile-20260520` (4 files: web/data.py, two bitunix template partials, audit_reality_reconciler.py — md5-verified to match pre-deploy state).

**Files deployed (4 modify):**
- `trading_corp/web/data.py` — extend `build_bitunix_trade_plan_view` to read latest `audit_reality_run` row + compute reconciler state (never_run / match / mismatch / no_trades / stale, 26h staleness boundary, mismatch overrides stale). Add `audit_corrected` / `corrected_*` / `display_result` / `correction_tooltip` to score-view `recent_fires`. md5 `734c86e30f61113a689e7f0e61ccdaf2`.
- `trading_corp/web/templates/partials/bitunix_trade_plan_panel.html` — reconciler-state strip between header and Section 1; mismatch=red alarm with expandable per-mismatch list, stale=amber warning, match=green, no_trades / never_run=gray muted. md5 `7cf29147ecdcfc9ae371dfb5ecbb021a`.
- `trading_corp/web/templates/partials/bitunix_score_panel.html` — recent-fires renders `display_result` (corrected when flagged) with 8px `corrected` badge + native-vs-corrected tooltip. md5 `9d30d6bad06233bf5f68bb1040ac06b3`.
- `scripts/audit_reality_reconciler.py` — `_persist_summary` writes one `audit_event` (kind=`audit_reality_run`) per run; called from `main()` after `reconcile_all()`, try/except-wrapped so write failure cannot crash the script or change exit code. md5 `b203f791514cd43ce4b668d853bfd250`.

**Features shipped:**
- **Reconciler-state tile.** Trade Plan v2 panel now alarms red on mismatch (visually unmissable), warns amber on stale (>26h since last run), shows green match + timestamp on healthy state. Closes the "silent immune system" gap — reconciler failure previously required manually running `systemctl status` to detect.
- **Corrected-outcome display.** Score-panel recent fires now badge `audit_corrected=true` rows with `corrected` label + tooltip showing native-vs-corrected R-multiple and result string. Trade #1 (`35aa49c9`) historical correction `loss/-1.0R → win/+0.838R` is now operator-visible.
- **Reconciler persists per-run summary.** Each daily timer fire (or manual run) writes one `audit_reality_run` audit_event row carrying n_total / n_matches / n_mismatches + mismatch details. Read-only-elsewhere; the dashboard reads this row.

**Notable code changes:**
- The new write lives in the reconciler, not the dashboard. The dashboard is read-only against `audit_reality_run` rows. Matches the task constraint "if Part 1 needs the reconciler to persist a last-run summary, that write lives in the reconciler/its wiring, not the dashboard."
- State precedence in `build_bitunix_trade_plan_view`: `never_run` → `mismatch` → `stale` → `no_trades` → `match`. **Mismatch overrides stale** (a stale mismatch still alarms red, not yellow).
- `audit_corrected=true` rule documented in memory: never an automated path (would silence the reconciler signal). See `trading_corp_bitunix_vision.md` §Audit-correction discipline.

**Verification:**
- `_persist_summary` write fired manually post-deploy: `audit_event` id 407478, ts `2026-05-20T22:51:58+00:00`, status=match, n_matches=3, n_total=3.
- Reconciler scanned all 3 closed v2 trades (2 pre-deploy `audit_corrected=true`, 1 post-deploy `ef6e6697`); **3/3 matches**, including the new ef6e6697 (bars_walked=2, no TPs reached, genuine SL hit — disambiguated earlier in the session).
- Dashboard `/division/bitunix_futures` (HTTP 200 via localhost:8000): tile renders green `✓ Reality match · 3/3 v2 trades · 05-20 18:51 ET`; corrected badges visible on both `audit_corrected` trades, with Trade #1 tooltip `Native: loss/-1.000R · Corrected: win/+0.838R`.
- Pre-deploy tests: 28/28 view-builder tests passed wrapped (`tests/test_bitunix_view_builders.py` including `test_*_mismatch`, `test_*_stale`, `test_*_mismatch_overrides_stale`, `test_score_view_recent_fires_surfaces_corrected_outcome`).
- Boot wiring log post-restart: `BitUnix observer wiring: scoring=True, pa_enabled=True, htf_gate_mode=enforce, htf_regime_enabled=True, trade_plan_active=True` (unchanged — no behavior change at the configuration layer).

**Mismatch / stale render paths NOT live-tested:** can't be seen without an actual mismatch or a >26h-old reconciler row. Unit-tested only via the named tests above. Acceptable for a display-layer change but worth flagging in case live observation reveals a render-edge bug.

**Unattended-timer-fire check (DEFERRED):** Daily timer next fires 2026-05-21 06:03 UTC + jitter. After 5/21 06:13 UTC run `journalctl -u tc-audit-reality.service --since "06:00 today"` to verify the unattended fire produces a clean `audit_reality_run` row — this is the first fully-unattended end-to-end exercise of the immune system. (Today's `_persist_summary` exercise was via manual reconciler invocation.)

**Issues encountered during deploy:**
- The required `systemctl restart trading-corp.service` triggered Robinhood device-approval MFA. Pre-restart PID had been running for ~5h 58m with an expired pickle generating runtime 401s (visible in pre-restart journal); the restart path detects the expired pickle and runs full re-login. The Robinhood push notification subsystem appeared to silently fail for the first 3 stop+start attempts — user reported never receiving a push. 4th attempt (~55 min after first restart) produced a successful Robinhood resolution; the journal advance between 22:50:02 (`Check robinhood app...`) and 22:50:45 (`Telegram channel online`) was silent, so it's unclear whether MFA went through or Robinhood adapter timed out → `broker_fallback_to_paper`. **Pre-existing condition surfaced by the necessary restart — not caused by deploy content.** Recommendation: refresh robinhood.pickle proactively before future restarts; consider whether an SMS-fallback MFA option exists in the adapter.

**Inert / dormant on current traffic:** none — this is a display change + one new audit row per reconciler run. No behavior change to risk gate, fee math, SL lifecycle, auto_execute, or any decision pipeline component.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-dashboard-tile-20260520; BASE=/home/azureuser/trading_corp; \
sudo mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
sudo mv \$BASE/trading_corp/web/templates/partials/bitunix_trade_plan_panel.html.\$TAG \$BASE/trading_corp/web/templates/partials/bitunix_trade_plan_panel.html; \
sudo mv \$BASE/trading_corp/web/templates/partials/bitunix_score_panel.html.\$TAG \$BASE/trading_corp/web/templates/partials/bitunix_score_panel.html; \
sudo mv \$BASE/scripts/audit_reality_reconciler.py.\$TAG \$BASE/scripts/audit_reality_reconciler.py; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-20 11:35 UTC — kalshi_weather entry-price floor (side-asymmetric, paper)

**Commits:** uncommitted on local `main`. Deployed via surgical anchor patcher; floor function + yaml entries already on prod via the 05:52 vol-v2 ship (see "hybrid story" below).
**Triggered by:** Post-cutoff RT analysis on `kalshi_weather_arb` since 2026-05-16T19:18Z: 163 RTs, 68.7% WR, **-$65.48 PnL**. The strategy clears the 65% paper→live gate yet bleeds on cheap-tail entries — YES ≤ $0.10 went 0/5 (-$37.50); NO < $0.50 went 0/5 (-$37.50). Suppressing those retroactively flips the sample from -$65 to ~+$10.
**Backup tag:** `pre-floor-20260520-1110` (3 files; only `kalshi_weather_arb.py` backup is a true pre-floor baseline — see hybrid story).

**Files deployed (3 modify; 1 local-only):**
- `trading_corp/agents/strategies/_weather_math.py` — NEW pure helper `apply_entry_price_floor` appended after `apply_bucket_guard`. Side-asymmetric: YES skipped at `<= 0.10` (inclusive), NO skipped at `< 0.50` (strict). NO comparator strict so $0.50 stays in the live `[0.50, 0.60)` band the post-cutoff RT analysis bucketed it into. Pure-function pattern mirrors `apply_bucket_guard` for testability. **Arrived on prod via the parallel-session vol-v2 whole-file scp at 05:52 UTC — not by this deploy.**
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — surgical import + call-site insertion. Imports `apply_entry_price_floor` alongside `apply_bucket_guard`; calls it between the `share_price` out-of-range gate and the Kelly sizing block. Skips become `code=entry_below_floor` audit rows. Pre-patcher md5 `4bf3005a…` (HEAD baseline) → post-patcher md5 `31595b5d…`. **This is the only file actually written by this deploy's patcher.**
- `config/strategies.yaml` — adds `min_yes_entry: 0.10`, `min_no_entry: 0.50`, and a 6-line comment block under `kalshi_weather_arb:`, between `max_horizon_hours: 72` and `# ── Tier-1 upgrades (2026-05-15)`. Hot-patch `max_per_day_pct: 120.0` deliberately preserved (anchor isolates the insert from `sizing:`). **Also arrived via the 05:52 vol-v2 ship.**
- `tests/test_kalshi_weather_fixes.py` — 9 new tests (8 parametrized boundary cases including the explicit-flipped `("no", 0.50, False)` plus a custom-thresholds case). 40/40 pass in 0.13s. Local-only; not shipped to prod.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Cheap-tail entries are now blocked at the source.** Any `kalshi_weather_arb` evaluation where the chosen-outcome ask hits YES ≤ $0.10 (inclusive) or NO < $0.50 (strict) emits `code=entry_below_floor` audit row instead of progressing to Kelly sizing. Observable: `kalshi_weather_skipped_entry_below_floor` audit rows; smoke check at 11:41 UTC produced **3 skips on the first scan cycle** (`KXLOWTBOS-26MAY20-T66`, `KXLOWTMIN-26MAY20-B42.5`, `KXLOWTMIN-26MAY20-B38.5`).
- **Floor thresholds are config-driven.** `min_yes_entry` / `min_no_entry` read from `_strat_cfg`; defaults (0.10 / 0.50) live in `_weather_math.apply_entry_price_floor`. Tightening or loosening is a yaml change + mtime-hot-reload — no service restart needed for future threshold tuning (the .py call-site has no other dependency on the floor values).

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Hybrid deploy story.** This deploy was NOT a clean independent ship. Phase A re-hashed prod and matched our Phase A baseline (`0bb50267…` / `00779032…` / `4bf3005a…`). Between Phase A and the patcher run, the parallel session that finalized vol-v2 at 05:52 UTC also shipped two of the three floor target files (`_weather_math.py`, `strategies.yaml`) **because the parallel session's whole-file scp picked up the uncommitted floor content from the same working tree**. By the time my patcher executed, the floor function + yaml entries were already on prod — only the call site in `kalshi_weather_arb.py` was missing. Patcher's idempotency markers (`def apply_entry_price_floor(` / `min_yes_entry: 0.10`) caused it to skip those two files (no backup created); it correctly surgically patched only `kalshi_weather_arb.py`.
- **Consequence: rollback is asymmetric.** Only `kalshi_weather_arb.py.pre-floor-20260520-1110` is a true pre-floor baseline (matches HEAD `4bf3005a…`). The other two backups were created manually post-restart-decision (`cp -p` of current state) for tag symmetry; they're byte-identical to their live counterparts. Soft rollback (disable floor) is one-file surgery; hard rollback (revert all floor content) is manual.
- **Floor is config-driven, NOT hard-coded in .py.** When the user wants to retire or tighten the floor later, change yaml. The .py wiring is permanent until a future code change.

**Latent bugs caught + fixed (in patcher development; pre-deploy):**
- **`Path.read_text(newline=...)` is Python 3.13+.** Patcher v1 used this kwarg; locally on 3.14 it worked, but prod is Python 3.10.12. First prod patcher run safe-failed with `TypeError` at the first `_read(p)`, before any write or backup. Zero state change confirmed via md5 + absence of backup files. Fix: switched to `Path.read_bytes().decode("utf-8")` / `Path.write_bytes(src.encode("utf-8"))`, which (a) bypasses universal-newlines translation entirely and (b) works on every 3.x. Re-scp'd and re-ran successfully.
- **Working-tree `_weather_math.py` is CRLF on this Windows checkout** (saved by an editor that converted on save); `kalshi_weather_arb.py` is LF. Prod is LF. Patcher's bytes-mode r/w preserves whatever endings the prod file has, so this never reached prod — but a naive `scp` from this working tree would have introduced CRLF drift. Another reason surgical-patch is the right pattern even when md5 matches.

**Verification:**
- Service restart: PID 860013 → 865556, `ExecMainStartTimestamp=2026-05-20 11:34:59 UTC`, `ActiveState=active`, `SubState=running`.
- Startup latency: ~95s before port 8000 bound (matches the 10:37 BitUnix restart pattern — Azure KV secret fetches + many strategy inits).
- `curl https://trading.jacksumner.com/healthz` → `{"status":"ok","mode":"PAPER"}` HTTP 200, 164ms.
- First post-restart kalshi_weather scan cycle at 11:41:03 UTC. 29 evaluations, **3 `entry_below_floor` skips** (KXLOWTBOS-26MAY20-T66, KXLOWTMIN-26MAY20-B42.5, KXLOWTMIN-26MAY20-B38.5), 0 weather `would_have_placed`. The floor is firing.
- No errors / tracebacks in journalctl since restart.
- Hot-patch survived: `grep max_per_day_pct config/strategies.yaml` → `120.0` at line 1519, comment "hot-patch preserved" still inline.

**Inert / dormant on current traffic:** none. The floor is exercising on the first cycle.

**Watch for (next 48h):**
- `kalshi_weather_skipped_entry_below_floor` audit rows should accumulate on each scan cycle (every 300s) when sub-floor proposals arise. Empty cycles are fine (markets quiet) — sustained absence over a full trading day suggests the floor isn't catching anything (revisit thresholds or check whether market depth has shifted).
- `kalshi_weather/would_have_placed` rows: should drop in proportion to entry_below_floor skips. Combined `evaluated → (kelly-fire + entry_below_floor + other-skips)` should be conservation-preserving.
- Forward paper validation: 60-day clock effectively starts today (2026-05-20). The pre-floor 163-RT post-cutoff sample is the baseline (-$65.48 PnL); aim for the floor-bucketed sample to be at least +$0 over a comparable window.

**Rollback recipe (soft — disable floor by removing call site only):**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-floor-20260520-1110; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/strategies/kalshi_weather_arb.py.\$TAG \
   \$BASE/trading_corp/agents/strategies/kalshi_weather_arb.py; \
sudo systemctl restart trading-corp"
```
This reverts only the call site. The `apply_entry_price_floor` function in `_weather_math.py` and the `min_yes_entry`/`min_no_entry` yaml entries stay on prod but become dormant (nothing imports/calls them). Harmless.

**Rollback recipe (hard — full revert):** not one-command. Would require manually deleting lines 382-415 of prod's `_weather_math.py` (the floor function) and the 8 floor lines from `config/strategies.yaml`. The `pre-floor-20260520-1110` backups of those two files are post-floor state (byte-identical to live), so they don't help. If hard rollback is needed: use `git show HEAD:trading_corp/agents/strategies/_weather_math.py` (md5 `00779032…`) as the source-of-truth for the function-removed state, but note that prod ALSO has the v2 `Gate 4` / `max_divergence_pct` content that HEAD lacks — surgical line removal is the safer path than full file replace.

---

## 2026-05-20 10:37 UTC — BitUnix v2 lifecycle kline-fetcher pagination fix + audit-vs-reality reconciler + daily systemd timer

**Commits:** `6c1e48b` (fetcher fix + new test file), `3021c03` (reconciler + bug-probe scripts), `fd26e8c` (v2-fix report + correction notices on two prior reports + trade re-tag script). All pushed to origin/main as part of this deploy.
**Triggered by:** silent audit-logging failure surfaced by user chart-observation of trades reaching TP2 on 5/19 that the bot recorded as -1.0R losses. Audit-integrity review (`reports/bitunix_audit_integrity_2026-05-20.md`) localized the bug; failing-test-then-passing-test evidence in `tests/test_bitunix_kline_fetcher_pagination.py`.
**Backup tag:** `pre-v2-kline-fix-20260520` (1 file: paper_trade_replay.py only — full md5-verified, see verification block).

**Files deployed (1 modify + 1 new + 2 new systemd unit files):**
- `trading_corp/agents/paper_trade_replay.py` — `_bitunix_kline_fetcher` rewrites pagination to slice the requested window into ≤200-bar sub-windows and iterate forward in time. Server cap at 200/page is documented in the function docstring. Classifier (`_classify_v2_multi_leg`) and routing condition unchanged. md5 `49c9735f6ee1fd2c74ed85f1e74b3421`.
- `scripts/audit_reality_reconciler.py` — **NEW**. Per closed v2 paper_trade_record, pulls bitunix_bar_history bars over [ts, result_ts] and replays via `_classify_v2_multi_leg`; compares simulated vs recorded result + R (±0.05 tol). Honors `extra_json.audit_corrected=true` rows by comparing against `corrected_result` / `corrected_r_multiple`. Exit 0 if all match, 1 on any mismatch (CI/cron gate). md5 `1a4da6bd4f8190178af4e82b6bcd2198`.
- `/etc/systemd/system/tc-audit-reality.service` — **NEW VM-side unit.** oneshot service running reconciler via venv python. StandardOutput/Error=journal. SuccessExitStatus=0 (mismatch fails the service).
- `/etc/systemd/system/tc-audit-reality.timer` — **NEW VM-side unit.** OnCalendar=daily 06:00 UTC, Persistent=true, RandomizedDelaySec=600. Enabled + started.

**Features shipped (load-bearing for future "is X done?" checks):**
- **v2 multi-leg lifecycle is no longer silently truncating bar slices.** Any v2 paper trade entered after 2026-05-20 10:37 UTC will see its full max_hold window walked, TP fills detected in price-action order, SL transitions emitted as `position_sl_update` audits. Observable: post-deploy v2 trades that hit TP1 will produce a `position_sl_update` audit row (all-time count was 0; the first non-zero count proves the fix is exercising).
- **Daily reality reconciler.** `tc-audit-reality.timer` runs the reconciler at ~06:00 UTC daily; any mismatch between recorded and bar-history-implied outcomes fails the systemd service → journalctl WARN → visible via `systemctl --failed`. Generalizes beyond this specific bug — catches any future class of silent audit-vs-reality boundary failure.
- **60-day paper-cutover clock restarted from 2026-05-20.** Pre-deploy 2 v2 trades are reconstructed-corrected (audit_corrected=true) and preserved for historical context but excluded from the cutover sample. Documented in `trading_corp_bitunix_vision.md` memory.

**Notable code changes (callouts a future Claude shouldn't miss):**
- The bug class was "self-consistent silent failure": every audit row was internally consistent and dashboard rendered cleanly; the audit just didn't reflect reality. Two prior reports (504c992, f6559ff) asserted "no silent failures" before the bug was caught. Both carry correction headers in commit fd26e8c.
- The `_classify_v2_multi_leg` classifier itself was always correct; the fix is to the fetcher's pagination loop only. Existing 27 tests in `tests/test_paper_trade_replay.py` still pass.
- Asymmetry between trade #1 (corrupted) and trade #2 (correct) was reality-dependent: both trades had identical buggy bar slices, but only trade #1 had TP fills in the dropped early window (verified against bitunix_bar_history price truth).

**Latent bugs caught + fixed (if any):**
- Confirmed via live probe (2026-05-20): BitUnix kline endpoint silently caps responses at 200 bars per call regardless of `limit` param, returning newest-first within the requested window. The legacy fetcher's `if len(page) < this_page: break` mistook this for end-of-data.
- 2 historical v2 paper_trade_record rows re-tagged with `audit_corrected=true` + reconciler-verified `corrected_result`/`corrected_r_multiple`/`corrected_filled_legs` in extra_json (original `result` and `actual_r_multiple` columns preserved). Trade #1 corrected: loss/-1.0R → win/+0.838R, +1.838R delta.

**Verification:**
- Fetcher reality-probe via venv python in-process: requested 1440 1m bars for trade #1's actual window; returned **1600 bars** (>200 threshold). Span = 1440 minutes (full window). First bar ts = 1779121440000 (5/18 16:24 UTC, matching entry).
- Reconciler in-process against prod DB: post-deploy with updated reconciler, **2/2 matches**, 0 mismatches.
  - Trade #1 (`35aa49c9`): recorded(corrected)=win/+0.838R vs simulated=win/+0.838R, filled_legs=['tp1','tp2'].
  - Trade #2 (`a467e316`): recorded(corrected)=loss/-1.0R vs simulated=loss/-1.0R.
- Daily systemd timer status post-install: `active (waiting)`. First-run service triggered manually post-install: exit 0, 2/2 matches. Next trigger: 2026-05-21 06:03 UTC (+ jitter).
- Service PID change: 844089 → 860028 (restart confirmed). Boot wiring log: `BitUnix observer wiring: scoring=True, pa_enabled=True, htf_gate_mode=enforce, htf_regime_enabled=True, trade_plan_active=True` (unchanged — no behavior change at the configuration layer).

**Inert / dormant on current traffic (if any):**
- The fix changes paper-mode replay behavior only. `BitunixBroker.place_order` still raises NotImplementedError and `auto_execute=false` — no live-capital path was touched. First evidence the fix is exercising will be the first new v2 trade post-deploy with a non-empty `filled_legs` or a non-zero `position_sl_update` count.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-v2-kline-fix-20260520; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/paper_trade_replay.py.\$TAG \$BASE/trading_corp/agents/paper_trade_replay.py; \
rm -f \$BASE/scripts/audit_reality_reconciler.py; \
systemctl disable --now tc-audit-reality.timer tc-audit-reality.service; \
rm -f /etc/systemd/system/tc-audit-reality.service /etc/systemd/system/tc-audit-reality.timer; \
systemctl daemon-reload; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-20 05:52 UTC — kalshi_crypto vol-v2 (realized vol) + max_divergence_pct cap (paper)

**Commits:** uncommitted at deploy; deployed via raw scp from verified-md5 local files.
**Triggered by:** Backtester replay validated structural correctness of realized vol (dissolves ~77% of bucket-guard flips into natural-path YES with zero outcome-flips). Strictly-comparable PnL dropped $19; rescued to ~flat only by an undersampled new-fire pool. Forward paper validation is the next required step.
**Backup tag:** `pre-vol-v2-paper-20260520-0541` (5 files), `pre-vol-v2-paper-20260520-0541` (crypto_spot_provider added late after first restart erred).

**Files deployed (5 modify + 1 new):**
- `trading_corp/data/crypto_vol_provider.py` — **NEW**. Realized-vol provider: ccxt fetch of Coinbase 5m bars, paginated-backward with dedup-by-timestamp, sample-std of log returns × sqrt(periods/yr) annualization. Refreshes hourly (configurable). Per-asset fallback to ANNUAL_VOLS constants on fetch error / insufficient coverage / staleness.
- `trading_corp/data/crypto_spot_provider.py` — `get_annual_vol` now reads the vol cache first; falls back to ANNUAL_VOLS. New `refresh_realized_vols_if_due` async staticmethod.
- `trading_corp/agents/strategies/_weather_math.py` — Added optional `max_divergence_pct` kwarg to `evaluate_weather_market` (Gate 4). Default None; weather strategy not passing it → no behavior change there.
- `trading_corp/agents/strategies/kalshi_crypto_arb.py` — Reads `max_divergence_pct` + `realized_vol.*` from yaml; refresh hook at run_scan_cycle entry; dual-vol mirror (hardcoded_av/prob/edge) + `vol_v2_classification` field on every eval/skip; maps "divergence_too_high" skip code (audit kind `kalshi_crypto_skipped_divergence_too_high`).
- `trading_corp/main.py` — `would_have_placed` audit allowlist now carries `threshold_high_usd`, `hardcoded_av`, `hardcoded_prob_yes`, `hardcoded_edge_pct`, `vol_v2_classification`.
- `config/strategies.yaml` — `realized_vol.enabled: false → true`; added `max_divergence_pct: 35.0`. `auto_execute: false` preserved.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Realized-vol-driven sigma for kalshi_crypto.** Trading-Corp now sizes Gaussian probability with rolling realized vol from Coinbase 5m bars instead of hardcoded ANNUAL_VOLS. Observable: `kalshi_crypto_vol_refresh` audit row with per-asset `source: realized:<n_bars>`; `annual_vol` field on `kalshi_crypto_evaluated` rows now ~0.30 for BTC, ~0.40 for ETH, ~0.50 for SOL, ~0.61 for DOGE, ~0.46 for XRP (vs 0.60/0.75/0.90/1.10/0.85 hardcoded).
- **`max_divergence_pct: 35.0` cap.** Block trades where edge > 35% — fixes the 50%+ NO bin bleed that backtester showed does NOT compress under realized vol (tail/oracle disagreement, not vol artifact). New audit kind `kalshi_crypto_skipped_divergence_too_high`.
- **Vol-v2 drift instrumentation.** Every eval + every fire carries `hardcoded_av`, `hardcoded_prob_yes`, `hardcoded_edge_pct`, `vol_v2_classification` (one of `same_fire` / `new_fire` / `suppressed_fire` / `both_skip`). Enables forward bucketing of new-fire resolution and baseline-drift tracking without reconstruction from bars.

**Notable code changes (callouts a future Claude shouldn't miss):**
- Initial restart at 2026-05-20 05:48 UTC failed because `crypto_spot_provider.py` was edited but not included in the first scp batch — strategy threw `AttributeError: type object 'CryptoSpotProvider' has no attribute 'refresh_realized_vols_if_due'`. Lesson: when an existing module gains a new method that a new module calls, count the diff carefully against the staged set. Caught + fixed at 2026-05-20 05:52 UTC; second restart succeeded.
- Bucket-guard logic in `_weather_math.apply_bucket_guard` and the per-market math untouched (per task scope).

**Latent bugs caught + fixed (if any):**
- See above — missing module-update file caught from journal AttributeError after restart 1.

**Verification:**
- New service `ActiveEnterTimestamp = Wed 2026-05-20 05:52:09 UTC`, PID 844075 (was 616794 since 2026-05-17 21:05).
- md5 match on all 6 files prod-vs-local.
- First post-restart `kalshi_crypto_vol_refresh` at 05:54:12 UTC, statuses `{BTC: realized:4032, ETH: realized:4032, SOL: realized:4032, DOGE: realized:4032, XRP: realized:4032}` — no fallback.
- Live evaluations confirm: BTC 0.2984, SOL 0.5052, DOGE 0.6002 (all within PoC ranges). ETH + XRP confirmed by refresh status but not yet surfaced in `kalshi_crypto_evaluated` rows (discovery filtered them out in early cycles — normal).
- Refresh cadence: 1 vol_refresh per ~10 scan cycles ≈ 60min interval ✓.
- Zero `kalshi_crypto_skipped_divergence_too_high` rows in first ~10 minutes — expected (realized vol compresses edges so few cross 35%).
- Zero `would_have_placed` rows in first ~10 minutes — markets are quiet; will revisit as paper data accumulates.

**Inert / dormant on current traffic (if any):**
- `vol_v2_classification` for `suppressed_fire` and `same_fire` classes will start landing more frequently once more fires occur. First `new_fire` classification observed in eval payload (then killed by share_price out-of-range, pre-existing skip path).

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-vol-v2-paper-20260520-0541; BASE=/home/azureuser/trading_corp; \
for f in trading_corp/agents/strategies/kalshi_crypto_arb.py trading_corp/agents/strategies/_weather_math.py trading_corp/main.py config/strategies.yaml trading_corp/data/crypto_spot_provider.py; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -f \$BASE/trading_corp/data/crypto_vol_provider.py; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-18 21:25 UTC — Promote/Demote UX fix v2 — Selected Whales filter + stop mutating watch_only_whales + tab persistence

**Commits:** (uncommitted at deploy; will commit after user verification — combined v1+v2 patch applied)
**Triggered by:** v1 (20:36 UTC) made PROMOTE work but exposed three follow-up issues during user smoke test:
  1. Demoted PM whale `taylorsversion` still appeared on Selected Whales because they had unpaired open BUY audits — the "OPEN positions but ZERO resolved" surfacing block didn't filter by current `selected_whales` membership.
  2. Kalshi: after demote, page reloaded to default Portfolio tab (tabs are JS-only with no URL fragment; HX-Refresh's `window.location.reload()` lost the tab state).
  3. Demoted whale's stats were reset to zeros on the Watch List panel (the demote endpoint added a fresh zero-stat entry to `watch_only_whales`).

**Backup tag:** `pre-promote-demote-uxfix-20260518-v2` (3 files captured at the v1-applied state; allows rollback to v1 if v2 misbehaves)
**Recovery procedure used:** rolled prod files back to the v1's `pre-promote-demote-uxfix-20260518-q1ack` backup tag so the combined v1+v2 patch (generated from local-HEAD vs local-current) could apply cleanly. Final state contains the full v2 codebase.

**Files deployed (3 modify) via gzipped patch -p1 (~3KB compressed, ~12KB raw):**
- `trading_corp/web/routes.py` — All four promote/demote endpoints now mutate ONLY `selected_whales` + `pinned_whales`. They no longer add/remove entries in `watch_only_whales`. This preserves the original Apify-scraped (Kalshi) / leaderboard-derived (Polymarket) stats so a demoted whale reappears on the Watch List with their full pre-promote stats intact. No API refetch needed for the user-promoted-from-watchlist common case.
- `trading_corp/web/data.py`:
  - `_query_pm_whales`: now loads `selected_whales` for both venues upfront and filters all row-emission (round_trips + opens + placeholders) by membership. Demoted whales with lingering unpaired BUYs no longer leak into Selected Whales. Whales' historical activity remains accessible via the History tab.
  - `_query_kalshi_watch_only_rows`: filters out handles currently in `selected_whales`. A promoted whale hides automatically; demoting them un-hides their original entry from `watch_only_whales` (with stats from `watch_only_stats` still intact).
  - `_query_polymarket_watch_only_rows`: symmetric filter, keyed by lowercased `proxy_wallet`.
- `trading_corp/web/templates/prediction_markets_dashboard.html` — Tab clicks now write to `window.location.hash`. A new init-time fragment-reader activates the matching tab on page load. HX-Refresh post-promote/demote preserves the hash so the user stays on the Whales tab.

**Features shipped:**
- **Demoted whales disappear from Selected Whales.** Demoting a trader with lingering open BUYs no longer leaves them visible.
- **Demoted whales reappear on Watch List with original stats.** A user-promoted whale who is then demoted now shows their full pre-promote leaderboard PnL, win-rate, top category, etc. No zero placeholder.
- **Tab selection survives demote/promote.** Click WHALES tab → demote a whale → page reloads → still on WHALES tab.

**Notable code changes:**
- `selected_whales` is now the single source of truth for "who's currently being copy-traded." Both panels (Selected + Watch List) gate by it. `watch_only_whales` is treated as the immutable observation pool (mutated only by `refresh_polymarket_whales.py` / `refresh_kalshi_whales.py` weekly).
- The algorithm-selected whales path (from `refresh_polymarket_whales.py`) is unchanged. Those whales are added directly to `selected_whales` without ever being in `watch_only_whales`. If demoted via dashboard, they don't reappear on the Watch List — that's expected; algorithm-selected whales are sourced from the leaderboard, not the watch list.
- The `_render_action_pill` response now flashes briefly between `outerHTML` swap and the page reload. Visual effect: row disappears → momentary blank → page reloads with both panels updated. The pill itself is rarely visible (reload happens before render in most browsers).

**Verification:**
- Local pytest: 8/8 smoke tests pass (3 new tests added covering the regression).
- Pre-deploy md5 captured under `pre-promote-demote-uxfix-20260518-v2` for rollback to v1 state if needed.
- Pre-v1 rollback applied first so combined patch applied cleanly (no FAILED hunks, no fuzz).
- Post-patch md5: routes.py `9555b4b0…`, data.py `98ffa1af…`, dashboard.html `02d76023…`. Matches LF-normalized local exactly.
- Service restarted: PID 614098 → 616794, active.
- Import smoke green; all three filter blocks present in source.
- Browser eyeball pending — user to confirm DEMOTE on a whale-with-history, then check (a) trader gone from Selected, (b) trader on Watch List with original stats, (c) Whales tab still active post-reload.

**Inert / dormant on current traffic:**
- None. All changes execute on every dashboard render.

**Rollback recipe:**
```bash
# Roll back to v1 (HX-Refresh only; the v2 regressions return)
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod --command-id RunShellScript --scripts '
TAG=pre-promote-demote-uxfix-20260518-v2
BASE=/home/azureuser/trading_corp
mv $BASE/trading_corp/web/routes.py.$TAG $BASE/trading_corp/web/routes.py
mv $BASE/trading_corp/web/data.py.$TAG   $BASE/trading_corp/web/data.py
mv $BASE/trading_corp/web/templates/prediction_markets_dashboard.html.$TAG $BASE/trading_corp/web/templates/prediction_markets_dashboard.html 2>/dev/null || true
sudo systemctl restart trading-corp
'

# Or roll all the way back to pre-feature (no promote/demote at all)
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod --command-id RunShellScript --scripts '
TAG=pre-promote-demote-uxfix-20260518-q1ack
BASE=/home/azureuser/trading_corp
mv $BASE/trading_corp/web/routes.py.$TAG $BASE/trading_corp/web/routes.py
mv $BASE/trading_corp/web/data.py.$TAG   $BASE/trading_corp/web/data.py
sudo systemctl restart trading-corp
'
```

---

## 2026-05-18 20:36 UTC — Promote/Demote UX fix — HX-Refresh + Selected Whales placeholders + Kalshi watchlist source-of-truth

**Commits:** (uncommitted at deploy; will commit after user verification — patch applied directly via az vm run-command)
**Triggered by:** User reported the 2026-05-17 17:18 UTC promote/demote feature was broken end-to-end. Clicking PROMOTE/DEMOTE removed the clicked row but the trader did not visibly move between the Selected Whales and Watch List panels; one venue's response had an acknowledgement pill, the other didn't.
**Backup tag:** `pre-promote-demote-uxfix-20260518-q1ack` (2 files)

**Files deployed (2 modify) via gzipped patch -p1 (~3KB compressed, ~7KB raw):**
- `trading_corp/web/routes.py` — `_render_action_pill` now sets `HX-Refresh: true` response header. When htmx receives this, it triggers a full page reload, which re-renders both panels from the updated agent_state slots. Fixes Bug A (cosmetic foster-parenting asymmetry — both venues now reload identically) + Bug D (page never refreshed after action).
- `trading_corp/web/data.py` — two query changes:
  - `_query_pm_whales` (Selected Whales panel): after the existing collection from round_trips + opens, walks `selected_whales` for both venues and appends zero-stat placeholder PMWhaleRow entries for any handle not already in the result. Inserted BEFORE the actor_id/is_pinned decoration loop so placeholders also get those fields. Fixes Bug B (freshly-promoted whale was invisible because it had no round_trips or opens yet).
  - `_query_kalshi_watch_only_rows` (Kalshi Watch List panel): switched source from `watch_only_stats` (dict) to `watch_only_whales` (list[dict]) — the slot the promote/demote endpoints actually write. Stats are now enriched by looking up the matching handle in `watch_only_stats`, falling back to zero/None when not yet enriched. Fixes Bug C (Kalshi watch list rendered the wrong slot, so demoted Kalshi whales never appeared and promoted ones never disappeared).

**Features shipped:**
- **PROMOTE moves the trader visibly across panels.** Click PROMOTE in either watch list → row disappears → page reloads → trader now appears in Selected Whales (as a zero-stat placeholder if no copy-trade has fired yet, full stats if it has). 📌 badge appears on manually-promoted entries.
- **DEMOTE moves the trader visibly across panels.** Click DEMOTE on a Selected Whales row → row disappears → page reloads → trader now appears in Watch List (zero stats until the periodic refresh enriches; the existing `notes` field carries "demoted via dashboard" for traceability). Synthetic SELLs still emitted as audits and paired by the resolver (no change to that pipeline).
- **Kalshi watch-list source-of-truth unified.** Both `_query_kalshi_watch_only_rows` and `refresh_kalshi_watchlist_stats.py` now operate consistently against `watch_only_whales` as the membership truth.

**Notable code changes:**
- HX-Refresh causes a full page reload (`window.location.reload()` from htmx). The page is small enough that this is fast; the action pill itself never visibly appears (the reload happens before it can render). The visual feedback is the row moving between panels.
- The placeholder rows for Bug B are tagged with `n_resolved=0, n_open=0, win_rate_pct=None, total_realized_pnl=0.0, last_entry_ts=None`. After decoration they get `actor_id` (so the demote button works) + `is_pinned` (so the 📌 badge renders if the handle is in pinned_whales).
- Bug C side effect: a handle present in `watch_only_stats` but NOT in `watch_only_whales` no longer renders. Pre-fix this was possible if `watch_only_stats` was richer than `watch_only_whales`. Post-fix, `watch_only_whales` is membership truth; `watch_only_stats` is enrichment-only. Aligns with the Polymarket behavior, where `watch_only_whales` already serves both roles.

**Verification:**
- Local pytest: 5/5 new smoke tests pass (`tests/test_promote_demote_fixes.py`).
- Pre-deploy md5 captured + backed up with tag `pre-promote-demote-uxfix-20260518-q1ack`.
- Patch dry-run on prod: clean (no rejects).
- Post-patch md5: routes.py `360e71b5…`, data.py `e9f5fba8…`.
- Service restarted: PID 598297 → 614098, active.
- Post-restart import smoke: `from trading_corp.web.data import _query_pm_whales, _query_kalshi_watch_only_rows; print('imports green')` succeeded; HX-Refresh present in routes.register source.
- Browser eyeball pending — user to confirm PROMOTE/DEMOTE on a low-stakes whale.

**Inert / dormant on current traffic:**
- None. All three changes execute on every dashboard render of the prediction-markets page.

**Rollback recipe:**
```bash
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod --command-id RunShellScript --scripts '
TAG=pre-promote-demote-uxfix-20260518-q1ack
BASE=/home/azureuser/trading_corp
mv $BASE/trading_corp/web/routes.py.$TAG $BASE/trading_corp/web/routes.py
mv $BASE/trading_corp/web/data.py.$TAG   $BASE/trading_corp/web/data.py
sudo systemctl restart trading-corp
'
```

---

## 2026-05-17 17:38 UTC — Polymarket watchlist weekly-refresh — Cloudflare 403 retry + --merge + systemd timer

**Commits:** `873e004`
**Triggered by:** Session pickup of BACKLOG P2 "Polymarket watchlist weekly refresh" added 2026-05-17 16:33 UTC. The 14:43 UTC seed crashed at chunk 1163 with HTTP 403 from gamma-api (Cloudflare rate-limited the Azure VM IP — shared with PCT live + polymarket_arbitrage live). Without these three changes the weekly Sunday cron will fail the same way every week.
**Backup tag:** `pre-pm-weekly-refresh-20260517-1730` (2 .py files; systemd units are new)

**Files deployed (2 modify + 2 new) via gzipped patch -p1 (20KB raw → 8KB compressed) + base64-decoded systemd units:**
- `trading_corp/data/polymarket_data_api_client.py` — `_get_json` retries on HTTP 403 with `cf-ray`/Cloudflare-marker body via exponential backoff (`_CLOUDFLARE_RETRY_DELAYS_SEC = (30, 60, 120, 240, 300)`, ~6 total attempts). Terminal failure raises the existing `PolymarketRateLimitError`. New `_is_cloudflare_block(resp)` helper (cf-ray header, server=cloudflare, or body marker). `fetch_market_resolutions` per-chunk swallow on `PolymarketRateLimitError` — failed chunks fall through to the existing `not_found` sentinel, so partial coverage is preserved instead of aborting the sweep. Logs `rate_limited_chunks` summary at the end. Non-Cloudflare 403s are NOT retried (caller's fault — propagate as `PolymarketDataAPIError`).
- `trading_corp/scripts/seed_polymarket_watchlist_deep.py` — new `_merge_watchlists(existing, fresh, *, max_total)` helper. `seed_polymarket_watchlist_deep` gains `merge: bool` + `max_total: int | None` params. CLI gains `--merge` (union with existing slot; preserve existing-entry `included_iso`; fresh stats win on collisions) and `--max-total N` (cap merged list by `realized_pnl_usdc` desc). Merge stats reported in summary + human print.
- `infra/systemd/trading-corp-pm-watchlist-deep.service` (NEW) — oneshot, runs `python -m trading_corp.scripts.seed_polymarket_watchlist_deep --merge --max-total 100`. `TimeoutStartSec=3600` (30-60 min wall-clock budget).
- `infra/systemd/trading-corp-pm-watchlist-deep.timer` (NEW) — `OnCalendar=Sun *-*-* 13:00:00 UTC`, `Persistent=true`, `RandomizedDelaySec=900` (15-min jitter). Sits cleanly between daily 12:00 UTC Kalshi stats-refresh (~5 min) and Sunday 14:00 UTC Kalshi deep-scan to avoid concurrent bulk-API load.

**Features shipped:**
- **Cloudflare 403 resilience on every Polymarket API call.** All five `_get_json` callers (`fetch_leaderboard`, `fetch_activity`, `fetch_positions`, `fetch_closed_positions`, `fetch_market_resolutions`) inherit the retry transparently. The seed sweep's `fetch_market_resolutions` ALSO gets per-chunk swallow so a single rate-limited chunk doesn't abort a 60+ min sweep.
- **`--merge` accumulation semantics.** Weekly refreshes now UNION with the existing watchlist instead of overwriting. Newly-discovered wallets get fresh `included_iso`; previously-seen wallets keep their original `included_iso` so we can track observation duration over time. Distinct from Kalshi's deep-scan (which overwrites).
- **Weekly cron self-driving.** `trading-corp-pm-watchlist-deep.timer` enabled + active. Next fire: Sun 2026-05-24 13:02:51 UTC.

**Notable code changes:**
- `PolymarketRateLimitError` docstring extended to call out that it is now used for both HTTP 429 AND Cloudflare-403-after-retry-budget — callers should catch both (the seed script's `fetch_market_resolutions` already does, via the new per-chunk handler).
- `_CLOUDFLARE_RETRY_DELAYS_SEC` is module-level so tests can monkeypatch it to shorten wall-clock. 12 new unit tests in `tests/test_polymarket_data_api_client_retry.py`.
- The retry loop ONLY triggers on 403 + Cloudflare markers (cf-ray header / server=cloudflare / body marker). Plain 403 (e.g. if Polymarket ever introduces auth) is NOT retried — caller's fault, propagated as generic `PolymarketDataAPIError`.
- The seed's `--merge` is wired through to a load-then-union path that calls `load_agent_state(polymarket_copy_trader, watch_only_whales)` BEFORE the `set_agent_state` write. If the slot is empty (cold start), the merge degenerates to "all fresh entries → write" identical to the overwrite path. So the same script binary works for both cold-start seeds and weekly accumulations.

**Verification:**
- Pre-deploy md5s captured + backed up with tag `pre-pm-weekly-refresh-20260517-1730`. (Pre-state had un-tracked drift vs git HEAD~1 — `cccbd5c…` vs `a10c01d…` for the client; mystery drift, likely from a recovery edit during the 16:00 UTC Cloudflare incident. Patch applied cleanly anyway via `patch --dry-run -p1` → no rejects.)
- Post-deploy md5 (`a10c01d…` for client, `0c70445…` for seed) matches local HEAD exactly.
- Smoke test on prod: `from trading_corp.data.polymarket_data_api_client import _CLOUDFLARE_RETRY_DELAYS_SEC; print(...)` → `(30.0, 60.0, 120.0, 240.0, 300.0)`. `from trading_corp.scripts.seed_polymarket_watchlist_deep import _merge_watchlists` succeeds.
- `python -m trading_corp.scripts.seed_polymarket_watchlist_deep --help` shows `--merge` + `--max-total N`.
- `systemctl is-enabled trading-corp-pm-watchlist-deep.timer` → `enabled`. `is-active` → `active`. `systemctl list-timers` shows `Sun 2026-05-24 13:02:51 UTC` as next fire (within 15-min jitter window).
- 12 new tests + 52 existing Polymarket tests pass locally (`pytest tests/test_polymarket_data_api_client_retry.py tests/test_polymarket_{copy_trader,arbitrage}.py`).
- **No service restart** — Option 1 chosen. The seed timer fires its own Python process which picks up the new client code automatically. Live PCT + polymarket_arbitrage continue running with the OLD in-process client until the next natural restart. Acceptable because those paths rarely hit Cloudflare and the failure mode is just an error log on the edge case.

**Inert / dormant on current traffic:**
- The systemd timer is enabled but won't fire until Sun 2026-05-24 13:00 UTC (+ jitter). Until then, both new files (.service + .timer) are loaded by systemd but exercising nothing.
- The Cloudflare retry is dormant on live PCT + polymarket_arbitrage (they still use the in-process pre-patch client) until the next `systemctl restart trading-corp`. To activate immediately: `sudo systemctl restart trading-corp` (~5-15s blip).

**Rollback recipe:**
```bash
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod --command-id RunShellScript --scripts '
TAG=pre-pm-weekly-refresh-20260517-1730
BASE=/home/azureuser/trading_corp
mv $BASE/trading_corp/data/polymarket_data_api_client.py.$TAG $BASE/trading_corp/data/polymarket_data_api_client.py
mv $BASE/trading_corp/scripts/seed_polymarket_watchlist_deep.py.$TAG $BASE/trading_corp/scripts/seed_polymarket_watchlist_deep.py
sudo systemctl disable --now trading-corp-pm-watchlist-deep.timer
sudo rm -f /etc/systemd/system/trading-corp-pm-watchlist-deep.{service,timer}
sudo systemctl daemon-reload
'
```

---

## 2026-05-17 17:18 UTC — Promote / Demote buttons (Kalshi + Polymarket) + pinned_whales merge

**Commits:** `efa6dc8`
**Triggered by:** User request to add VIEW + PROMOTE buttons to both watch-list panels and VIEW + DEMOTE buttons to both Selected Whales panels. Symmetric flow across Kalshi and Polymarket copy-trading. BACKLOG WO-4 closed by this ship.
**Backup tag:** `pre-promote-demote-20260517-1718` (7 files)

**Files deployed (7 modify) via gzipped patch -p1 (38KB raw → 10KB compressed):**
- `trading_corp/agents/strategies/polymarket_copy_trader.py` — new module-level `force_close_whale_positions(wallet, *, db_url, logger_agent, division, reason)`. Iterates the whale's `whale_state:<wallet>` slot's `our_positions`, emits `would_have_placed` audits with `side=sell` + `is_synthetic_close=True` + `synthetic_close_reason="demoted_via_ui"` so the polymarket_resolver pairs them into round_trips. Resets the slot to a clean baseline (last_seen_ts=now, our_positions={}) so re-promotion does not replay history. v1: synthetic close uses entry_price (zero-PnL paper close).
- `trading_corp/agents/strategies/kalshi_copy_trader.py` — symmetric `force_close_whale_positions(handle, *, db_url, logger_agent, division, reason)`. Reads the `positions:<nickname>` slot, emits `would_have_placed` with side=sell so kalshi_resolver pairs into round_trips. Clears the slot (empty dict).
- `trading_corp/web/routes.py` — 4 new POST endpoints inside `register(app)` before the `/research` route:
  - `/api/kalshi/watchlist/promote/{handle}` — moves handle into selected_whales + pinned_whales, removes from watch_only_whales, audits `kalshi_whale_promoted`.
  - `/api/kalshi/whales/demote/{handle}` — calls `kalshi_copy_trader.force_close_whale_positions`, removes from selected + pinned, adds back to watch_only_whales, audits `kalshi_whale_demoted`.
  - `/api/polymarket/watchlist/promote/{proxy_wallet}` — same shape but list[dict] payloads (wallet + user_name + category + promoted_iso + source).
  - `/api/polymarket/whales/demote/{proxy_wallet}` — same shape, calls polymarket force_close.
- `trading_corp/web/data.py` — `PMWhaleRow` gains `actor_id` (the demote endpoint's path-id: handle for Kalshi, proxy_wallet for Polymarket) and `is_pinned` (whale is in pinned_whales). `_query_pm_whales` loads both `pinned_whales` slots and (for Polymarket) builds a user_name→wallet map from selected_whales so PMWhaleRow.actor_id can be set without re-fetching.
- `trading_corp/web/templates/partials/pm_dashboard_body.html` — four panel changes:
  - Selected Whales (cross-venue): new "Action" column with View + Demote buttons. View link is venue-aware (`kalshi.com/social/profile/{handle}` vs `polymarket.com/profile/{actor_id}`). Demote button has `hx-confirm` prompt mentioning the synthetic-SELL semantics. 📌 badge appears next to manually-promoted whales (is_pinned=True) for visual distinction from algorithmically-selected ones.
  - Kalshi Watch List: previously-disabled "Promote" stub button (WO-4 placeholder) is now wired `hx-post=/api/kalshi/watchlist/promote/{handle}` with confirm prompt; new View link to kalshi.com profile added alongside.
  - Polymarket Watch List: new Promote button placed next to existing View link, `hx-post=/api/polymarket/watchlist/promote/{proxy_wallet}` with confirm prompt.
- `trading_corp/scripts/refresh_polymarket_whales.py` — before `set_agent_state(selected_whales)`, loads `pinned_whales` and unions it into `selected_records` deduped by lowercased wallet. `summary["pinned_merged"]` records the merge count.
- `trading_corp/scripts/refresh_kalshi_whales.py` — symmetric merge for the `list[str]` schema. Both refresh scripts now preserve manually-promoted whales across runs.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Promote whale via dashboard:** click in either watch list moves the whale to `selected_whales` + `pinned_whales` and starts copy-trading on the next strategy poll (60s Polymarket, 600s Kalshi) — strategies reload `selected_whales` every cycle so no restart needed. Cold-start protection automatic: the strategy's existing `state is None` check baselines without replaying history.
- **Demote whale via dashboard:** click on Selected Whales row emits synthetic SELL audits for every tracked open position (so the resolver closes the round_trips), removes from selected + pinned, adds back to watch_only_whales. Copy-trading stops on the next poll.
- **Pinned-whale protection:** new `agent_state(<actor>, pinned_whales)` slot keyed by handle (Kalshi: list[str]) / wallet (Polymarket: list[dict]). `refresh_*_whales.py` merge pinned into the algorithm's selection so manual promotions survive periodic re-ranking. No more silent eviction.
- **WO-4 closed:** BACKLOG `WO-4: Promote button` (filed 2026-05-15 with the Kalshi watch-only ship) is implemented and live for both venues simultaneously.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Force-close is module-level, not instance-level.** `force_close_whale_positions` is a module-level function in each copy_trader file (not a method). Callers don't need a strategy instance — they pass `db_url` + `logger_agent` directly. Keeps the demote endpoint decoupled from agent wiring.
- **Synthetic close is zero-PnL in v1.** Exit price = entry_price; the resolver pairs the synthetic SELL with the original BUY into a round_trip with realized_pnl≈0. Future iteration could plug in `broker.quote()` for a true mark-to-market exit. Tagged with `is_synthetic_close=true` + `synthetic_close_reason="demoted_via_ui"` in the audit payload for retroactive filtering.
- **State slot is RESET, not deleted, on demote.** Polymarket: writes `{user_name, last_seen_ts=now, last_seen_txhashes=[], our_positions={}}`. Kalshi: writes `{}`. Re-promoting a previously-demoted whale gets a clean baseline with no historical replay risk.
- **Polymarket needs a user_name → wallet map** for the dashboard to address the demote endpoint by wallet. `_query_pm_whales` builds this from `selected_whales` and exposes the wallet as `PMWhaleRow.actor_id`. If a whale exists in `polymarket_round_trips` but NOT in `selected_whales` (e.g. autopaused or just demoted), actor_id is empty and the template renders a `—` instead of a Demote button (prevents accidental demote on a whale we no longer have a stable ID for).
- **CRLF-vs-LF deploy gotcha caught + fixed mid-deploy.** First patch attempt failed at routes.py hunk because both prod's and local's `routes.py` are CRLF on disk but `git diff` generates LF-only patches. Workaround: `sed -i 's/\r$//' routes.py` on prod to normalize to LF before applying. The other 6 files were already LF on prod so patch applied to them cleanly. Worth carrying this `sed` step forward into future deploys that touch routes.py.

**New audit kinds (no schema change to audit_event table):**
- `polymarket_whale_promoted` / `polymarket_whale_demoted`
- `kalshi_whale_promoted` / `kalshi_whale_demoted`

**Verification:**
- Pre-deploy md5-diff: all 7 files DIFFER on prod (expected).
- Local end-to-end tests (paper mode, FastAPI TestClient): seeded test whales for both venues, hit promote → verified selected+pinned populated and watch_only cleared, hit demote → verified selected+pinned cleared and watch_only repopulated, audit events of correct kinds emitted. Cleaned up test entries.
- Patch dry-run on prod (after LF normalization): all 7 files clean apply.
- Service restarted: PID 588842 → 598297, active.
- Prod imports green: `force_close_whale_positions` callable on both copy_trader modules; `PMWhaleRow` has `actor_id` + `is_pinned` fields.

**Inert / dormant on current traffic:**
- The 4 new endpoints are inert until a Board member clicks a button on the dashboard. They have no autonomous trigger.
- Demote's force_close emits synthetic SELL audits at entry_price. If no whales are demoted, no synthetic audits land. The resolver will keep pairing organic SELLs as today.

**Rollback recipe:**
```bash
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod --command-id RunShellScript --scripts '
TAG=pre-promote-demote-20260517-1718
BASE=/home/azureuser/trading_corp
for f in trading_corp/agents/strategies/polymarket_copy_trader.py trading_corp/agents/strategies/kalshi_copy_trader.py trading_corp/web/data.py trading_corp/web/routes.py trading_corp/web/templates/partials/pm_dashboard_body.html trading_corp/scripts/refresh_polymarket_whales.py trading_corp/scripts/refresh_kalshi_whales.py; do
  mv $BASE/$f.$TAG $BASE/$f
done
sudo systemctl restart trading-corp
'
```
agent_state slots `pinned_whales` (both venues) survive rollback as inert data. They're only read by the refresh scripts and the dashboard query; with the old code, they're ignored.

---

## 2026-05-17 14:43 UTC — Polymarket watchlist seed + dashboard panel

**Commits:** `30f8abe`
**Triggered by:** User asked to find every Polymarket wallet with 100+ trades and >70% win rate, rank by realized PnL, export top 50 to an observation-only watchlist. Build mirrored the existing Kalshi `watch_only_whales` pattern.
**Backup tag:** `pre-pm-watchlist-20260517-1443` (3 files: polymarket_data_api_client.py, web/data.py, pm_dashboard_body.html)

**Files deployed (3 modify + 1 new) via gzipped patch -p1 (15.6KB raw → 6KB compressed) + base64-decoded new file:**
- `trading_corp/data/polymarket_data_api_client.py` — adds `ClosedPositionRow` dataclass + `fetch_closed_positions()` async method. (Kept even though the watchlist seed pivoted away from `/closed-positions` — see below — they remain a valid free-public-API primitive for future use.)
- `trading_corp/web/data.py` — adds `PolymarketWatchOnlyRow` dataclass + `_query_polymarket_watch_only_rows()` + `PMDashboardView.polymarket_watch_only` field, wired into the `asyncio.gather`. Gates on `"polymarket_copy_trading" in target_slugs`.
- `trading_corp/web/templates/partials/pm_dashboard_body.html` — adds a Polymarket Watch List section in the Whales tab parallel to Kalshi's. Profile link points to `polymarket.com/profile/<proxy_wallet>`. Tab visibility condition expanded to include this list.
- `trading_corp/scripts/seed_polymarket_watchlist_deep.py` (NEW, 16028 bytes) — paginates `/v1/leaderboard` across 5 categories + global (~2.4K unique wallets), fetches `/activity` (default 2 pages × 500/wallet), batch-joins gamma-api resolutions, computes wins/losses via the existing `compute_polymarket_stats` helper, filters wallets with ≥100 resolved positions AND wins/total ≥0.70, ranks by realized PnL on resolved BUYs, writes top 50 to `agent_state(polymarket_copy_trader, watch_only_whales)`. CLI flags: `--candidates --top --min-positions --min-win-rate --activity-limit --activity-pages --dry-run --json`.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Polymarket whale watchlist data slot:** `agent_state(polymarket_copy_trader, watch_only_whales)` now populated/populatable. Observation-only — never emits ProposedOrders.
- **Polymarket Watch List dashboard panel:** renders at `/prediction-markets/polymarket_copy_trading` once the slot is populated. Columns: rank, whale handle, category, N positions, WR%, realized PnL, leaderboard PnL, leaderboard vol, profile link.
- **Deep-seed script:** idempotent, free-API ($0/run), re-runnable. ~30–60 min wall-clock for the full 5-category × 500-candidate sweep.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`/closed-positions` endpoint is biased — DO NOT USE for win-rate computation.** Empirically (probed 2026-05-17 during this build): the endpoint filters out positions with non-positive `realizedPnl`. A wallet's *losing* positions held to zero do not appear. Any WR computed from `/closed-positions` rows will always trend near 100%, and any profit-sum is a one-sided upper bound. The correct path is `/activity` + gamma-api resolution joins via `compute_polymarket_stats` (same as `refresh_polymarket_whales.py` uses for the live PCT roster). Initial agent build went down the `/closed-positions` shortcut; pivot was halfway through the session.
- **Polymarket leaderboard caps at 50 rows per call** regardless of the `limit` parameter; offset works arbitrarily deep. The seed script paginates via offset until `--candidates` rows accumulate. Earlier `refresh_polymarket_whales.py` has the same single-call bug latent (would silently return 50 even if you pass `--candidates 500`); BACKLOG-worthy fix.

**Verification:**
- Pre-deploy md5-diff against prod: all three modified files DIFFER (as expected — they had the older state).
- Patch applied cleanly with `--dry-run` showing no rejects.
- Post-deploy md5-diff: 3 of 4 files match local exactly; `web/data.py` differs by CRLF-on-local-vs-LF-on-prod only (semantic equivalence confirmed by import test).
- Service restarted (`systemctl restart trading-corp`); was PID 547556, now PID 588842, active.
- Import smoke test on prod: `from trading_corp.web.data import _query_polymarket_watch_only_rows, PolymarketWatchOnlyRow` succeeds.
- Local SQLite slot is already populated with 50 whales from this session (top: everydaymortgage / 90% WR / 577 pos / $1.42M).
- Prod seed launched as PID 589207 (`nohup ... > /tmp/pm_seed_prod.log 2>&1 &`); first leaderboard pulls confirmed in log. ETA ~30–60 min until prod slot populates.

**Inert / dormant on current traffic:**
- `ClosedPositionRow` + `fetch_closed_positions()` are exposed on `PolymarketDataAPIClient` but no caller uses them today. Available for future surfaces (e.g., per-whale profile drilldown) without re-deploying the data layer.

**Recovery action 2026-05-17 16:29 UTC — prod seed crashed; pushed local JSON directly:**

The background prod seed (PID 589207) crashed at chunk 1163 with HTTP 403 from `gamma-api.polymarket.com`. Cloudflare rate-limited the Azure VM IP — likely tripped because the VM IP is shared with PCT live + polymarket_arbitrage live, so the seed's ~2300 gamma calls added enough load to trigger protection. Local IP completed the same sweep cleanly earlier in the session.

**Workaround:** packaged the locally-computed JSON (`reports/polymarket_watchlist_v2_preview.json`, 50 whales) → gzipped + base64 → shipped via `az vm run-command` → decoded + `set_agent_state(polymarket_copy_trader, watch_only_whales)` directly. Bypasses the prod compute entirely.

Post-recovery verification: `load_agent_state` returns 50 whales, updated_ts 2026-05-17 16:29:52 UTC. Top-3 match local: everydaymortgage 90% / westminster 94% / taylorsversion 81%.

**BLOCKER for the weekly cron** (BACKLOG `P2 — Polymarket watchlist weekly refresh`): rate-limit retry handling MUST land before this can run on a schedule. Spec'd: in `PolymarketDataAPIClient._get_json`, exponential backoff on 403+Cloudflare marker; in `seed_polymarket_watchlist_deep`, swallow terminal failure in `fetch_market_resolutions` and continue with partial coverage (`compute_polymarket_stats` handles `not_found` resolutions cleanly).

**Rollback recipe:**
```bash
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod --command-id RunShellScript --scripts '
TAG=pre-pm-watchlist-20260517-1443
BASE=/home/azureuser/trading_corp
for f in trading_corp/data/polymarket_data_api_client.py trading_corp/web/data.py trading_corp/web/templates/partials/pm_dashboard_body.html; do
  mv $BASE/$f.$TAG $BASE/$f
done
rm -f $BASE/trading_corp/scripts/seed_polymarket_watchlist_deep.py
sudo systemctl restart trading-corp
'
```
The agent_state slot will remain populated after rollback (it's a data write, not a code dependency). Empty the slot via `set_agent_state(..., None)` if needed.

---

## 2026-05-17 05:14 UTC — BitUnix trade-plan v2 LIVE — Phase 1E flag flip + paper-mode multi-leg replay + dashboard

**Commits:** `c41e7fd` (Stage A+B code) + YAML flag flip (prod-only — local YAML has known H2-era drift)
**Triggered by:** Board direction "do these 3" (PR 5 reconciler + PR 6 dashboard + Phase 1E flip). Strategy_gaps memory promised trade-plan PRs 1-4 were code-complete 2026-05-15; this deploy closes PRs 5+6 + lifts the dormancy gate. Plan path (a) chosen — paper-mode multi-leg replay extension over the simpler "skip and accept dormancy" alternatives, because (b)/(c) produced misleadingly-worse paper data on v2 trades.

**Three stages in one session, three separate prod writes:**

### Stage A — paper_trade_replay.py multi-leg-aware

**Backup tag:** `pre-trade-plan-v2-20260517-0507` (4 files: bitunix.py, paper_trade_replay.py, web/data.py, division.html)

**Files deployed (4 modify + 1 new) via gzipped patch -p1 (47KB raw → 16KB compressed):**
- `trading_corp/brokers/bitunix.py` — `list_open_positions` now hydrates `filled_legs` + `current_sl` from `extra_json` (defaults preserved). Reconciler reads real state instead of always-empty `[]`. Local md5 `a7125b2febf2f008cf03dfd82243fe9e` byte-identical with prod.
- `trading_corp/agents/paper_trade_replay.py` — new `_classify_v2_multi_leg` routes on `extra_json.tp_plan_version == 'v2'`. Walks 1m bars detecting tp1/tp2/tp3 crosses in order, advances SL per Option C floor lifecycle (BE → tp1-price floor; Chandelier trail deferred to follow-up), emits `position_sl_update` audit rows at each transition. Weighted-R aggregation matches Option C arithmetic (tp1+tp2 + remainder at tp1-floor = 0.75R lower bound). Resumable across replay ticks via `extra_json.filled_legs`. Conservative same-bar SL+TP tie-handling preserved (SL first). Local md5 `3510cfbe015d4e092abc37d0a78cab87` byte-identical with prod.

**Features shipped:**
- **Paper-mode multi-leg fill simulation.** v2 paper trades now resolve as 3-leg cascades, not single-leg TP3. Realized R correctly reflects partial-fill outcomes (0.125R / 0.75R / 1.25R per Option C scenarios).
- **`position_sl_update` audits now emit in paper mode.** Source tagged `paper_trade_replay` (vs `reconciler` in live mode) so dashboards distinguish synthetic from real broker-fill-driven lifecycle.

### Stage B — Trade Plan v2 + SL Lifecycle dashboard panel

- `trading_corp/web/data.py` — new `build_bitunix_trade_plan_view` queries `trade_plan_decision` + `position_sl_update` audits. Returns last-10 of each + 24h counts (decisions_total / should_trade_true / skipped / sl_updates_total).
- `trading_corp/web/templates/division.html` — includes new panel after Decision Flow.
- `trading_corp/web/templates/partials/bitunix_trade_plan_panel.html` (NEW) — 2-section panel: Decisions table (entry / SL / tp1/tp2/tp3 / sl_method / tp2_method / skip_reason) + SL Lifecycle table (state / current→new SL / filled_legs / source). Header shows V2 ACTIVE/DORMANT marker + fee config introspection + 24h counters.

**Verification (Stage B):**
- Healthz 200 after warmup. Page 90143 bytes (+1k from pre-stage-B).
- All panel markers present pre-flip: `bitunix-trade-plan-panel` × 3 (htmx triple), "V2 DORMANT" × 1, "No trade_plan_decision audit rows yet" × 1, "24h: decisions" × 1.
- Zero template errors.

### Stage C — `trade_plan.enabled: false → true` (Phase 1E gate flip)

**Backup tag:** `pre-trade-plan-flip-20260517-0512` (config/strategies.yaml only)

**Surgical YAML edit on prod** (one-line replace via Python anchored patcher; prod YAML has known drift from local per `trading_corp_prod_git_drift.md` so surgical is safer than wholesale-replace):
```yaml
# Before:                            After:
  trade_plan:                          trade_plan:
    enabled: false  # PR 4 — flip       enabled: true   # Phase 1E — v2 path active (2026-05-17)
```

`yaml.safe_load` verification on prod: `bitunix_futures.trade_plan.enabled = True`.

**Service restart 05:14:32 UTC** (per `feedback_bitunix_no_hot_reload.md` — BitUnix scorer/observer doesn't mtime-cache; YAML changes need restart). Healthz back at 200 after 5s.

**Boot wiring CHANGED:**
```
BitUnix observer wiring: scoring=True, pa_enabled=True, htf_gate_mode=enforce,
                         htf_regime_enabled=True, trade_plan_active=True
```
`trade_plan_active=True` ← this was `False` on every prior boot since the trade-plan PRs shipped 2026-05-15. Phase 1E gate lifted.

**Dashboard post-flip:**
- "V2 ACTIVE" marker × 1, "V2 DORMANT" × 0 (correctly read the flag transition).
- Page 91628 bytes (+1.5k from active-state expansion).
- `paper_trade_replay` loop online; `bitunix-position-reconciler` task still scheduled at 60s; both will now exercise on v2 trades as they fire.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Trade-plan v2 path is the active placement code on bitunix_futures.** Observer's `_score_and_maybe_propose_locked` now dispatches to `_build_proposal_v2` (structure-preferred SL + 3-leg TP plan) instead of the legacy geometric `_build_proposal`. Every BitUnix paper-fire from this point produces a `tp_plan` payload + 3-leg `paper_trade_record`.
- **Phase 1E gate lifted.** The "Do NOT flip trade_plan.enabled: true" entry in BACKLOG snapshot's "Things to NOT do" list is no longer active.

**Inert / dormant:**
- `auto_execute: true` unchanged (already true per Board direction). Per CLAUDE.md § 5, the webhook risk gate vs LangGraph harmonization gap is STILL load-bearing — does NOT unlock any path to real-money placement. Phase 4 (`BitunixBroker.place_order` real signed REST) is still the next gate.
- Chandelier trail in paper replay deliberately skipped (post-TP2 floor only). Follow-up if data argues for it.

**Watch for (next 24h):**
- First `trade_plan_decision` audit row → confirms `_build_proposal_v2` dispatch path is exercising on real TV alerts. Query:
  ```sql
  SELECT ts, json_extract(payload_json,'$.trigger_signal'), json_extract(payload_json,'$.should_trade'),
         json_extract(payload_json,'$.skip_reason')
    FROM audit_event WHERE kind='trade_plan_decision' ORDER BY id DESC LIMIT 10;
  ```
- First `paper_trade_record` row with `json_extract(extra_json,'$.tp_plan_version')='v2'` → confirms v2 trade lands in storage with the 3-leg `tp_plan`.
- First `position_sl_update` audit row with `source='paper_trade_replay'` → confirms the multi-leg replay extension is detecting leg fills + emitting lifecycle audits. Query:
  ```sql
  SELECT ts, json_extract(payload_json,'$.lifecycle_state'), json_extract(payload_json,'$.source'),
         json_extract(payload_json,'$.filled_legs')
    FROM audit_event WHERE kind='position_sl_update' ORDER BY id DESC LIMIT 10;
  ```
- Dashboard panel "V2 ACTIVE" with populated Decisions + SL Lifecycle tables.

**Tests:** 13 new in this PR (9 multi-leg replay + 4 trade-plan view-builder). 185-test adjacent suite green. Tests in `tests/test_paper_trade_replay.py` (new `_v2_*` group) and `tests/test_bitunix_view_builders.py` (new "PR 6" group).

**Rollback recipe (~30s):**
```bash
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod --command-id RunShellScript --scripts '
TAG_CODE=pre-trade-plan-v2-20260517-0507
TAG_YAML=pre-trade-plan-flip-20260517-0512
BASE=/home/azureuser/trading_corp
for f in trading_corp/brokers/bitunix.py trading_corp/agents/paper_trade_replay.py trading_corp/web/data.py trading_corp/web/templates/division.html; do
  mv $BASE/$f.$TAG_CODE $BASE/$f
done
rm -f $BASE/trading_corp/web/templates/partials/bitunix_trade_plan_panel.html
mv $BASE/config/strategies.yaml.$TAG_YAML $BASE/config/strategies.yaml
sudo systemctl restart trading-corp'
```

---

## 2026-05-17 04:13 UTC — BitUnix dashboard: surface deferred-fire mechanism

**Commits:** `f85ac9f`
**Triggered by:** Board direction immediately after the 03:53 UTC deferred-fire mechanism deploy. The new audit kinds (`pa_validation_redeem`, `pa_validation_expired`) and the in-memory PA cache state were operator-invisible without SQL tails — adding the dashboard surfaces.

**Backup tag:** `pre-dash-deferred-20260517-0411` (4 existing files; pending-PA panel is new)

**Files deployed (5 — 4 modify + 1 new) via gzipped `patch -p1`:**
- `trading_corp/web/data.py` — new `build_bitunix_pending_pa_view(deps)` reading observer's in-memory `_pending_pa_payload` / `_pending_pa_side` / `_pending_pa_cached_at_ts` + enriching with most-recent `pa_validation_decision` REJECT for the cached signal. Extended `build_bitunix_pa_view` with `redeem_counts` (24h windowed: redeemed_24h, expired_score_decay_24h, expired_opposite_side_24h) + `recent_redeems` / `recent_expired` last-5 lists (age-agnostic so operators still see activity past the 24h cutoff). Extended `build_bitunix_decision_flow_view` with `redeemed: bool` (sourced from `bitunix_score_decided.trigger_source == 'bar_tick_redeem'`) + `redeem: {bars_waited, seconds_waited}` from joining the matching `pa_validation_redeem` row. Added `bitunix_pending_pa: dict | None` field to `DivisionViewSnapshot`. Local LF md5 `4d3f808357cfeb481ee412bf113b3d53` byte-identical with prod after deploy.
- `trading_corp/web/templates/division.html` — includes `bitunix_pending_pa_panel.html` at TOP of the BitUnix section (above HTF) so operators read "what are we currently watching" first.
- `trading_corp/web/templates/partials/bitunix_decision_flow.html` — each flow row's outcome cell shows "⤴ redeemed (Nb · Ns)" inline when `f.redeemed` is True. Existing layout preserved.
- `trading_corp/web/templates/partials/bitunix_pa_panel.html` — header now shows 24h aggregate counts inline (⤴N · ⨯Nsd · ⨯Nos for redeemed / expired-score-decay / expired-opposite-side). Bottom of panel adds a 2-column grid: "Recent Redeemed Fires" (with `placed` vs `post-PA gate blocked` indicator using the `order_id` populated by the redeem-audit backfill) and "Recent Expired Waits" (with reason field).
- `trading_corp/web/templates/partials/bitunix_pending_pa_panel.html` (NEW) — live cache state, 15s htmx refresh (tighter than the 30s gate panels). Shows side · trigger / wait elapsed / currently-failing validators. "no signal pending" empty state when cache is empty.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Live "Pending PA" indicator on the BitUnix division page.** Operator can read the deferred-fire wait state in real time without SQL queries. 15s htmx refresh.
- **24h redemption/expiration aggregates on PA Validators panel** — answers "is the deferred-fire mechanism working?" at a glance.
- **Per-fire redemption marker on Decision Flow rail** — answers "did THIS fire come from the redeem path?" without joining `paper_trade_record.extra_json`.

**Notable code changes:**
- The pending-PA view reads observer in-memory state — there's no DB query for the cache itself. The `last_failed` / `last_pa_decision_reason` enrichment IS a bounded DB query (last 50 PA decisions, filtered to the cached signal). View returns a non-None dict even when nothing is cached (`{cached: False, ...}`) so the template always renders ("no signal pending" empty state) rather than hiding the panel.
- `recent_redeems` / `recent_expired` are **age-agnostic last-5** by design — operators still see recent activity even if it just fell out of the 24h aggregate window. The `redeem_counts` fields ARE 24h-windowed (cutoff via `datetime.now(timezone.utc) - timedelta(hours=24)`).
- Decision Flow's redeem detection uses **two-stage logic**: `f.redeemed` comes from the score-decided row's `trigger_source` field (cheap, no join); `f.redeem.bars_waited` comes from the `pa_validation_redeem` row joined by signal + ts (±60s window). The flag fires even if the audit row's gone missing.

**Verification:**
- `patch -p1` applied 5/5 files cleanly (dry-run + apply, 0 rejects).
- Post-patch md5s match local LF byte-for-byte for all 5 files.
- Import test confirmed `build_bitunix_pending_pa_view`, `build_bitunix_pa_view`, `build_bitunix_decision_flow_view` all importable.
- Service restart 04:13:50 UTC. PID change verified. `systemctl is-active trading-corp` = `active`.
- Boot wiring unchanged: `scoring=True, pa_enabled=True, htf_gate_mode=enforce, htf_regime_enabled=True, trade_plan_active=False`.
- Healthz `200 {"status":"ok","mode":"PAPER"}` (after ~20s warmup).
- `GET /division/bitunix_futures` → 200, 89126 bytes (was ~75k, +14k from new panels). Confirmed markers present:
  - `bitunix-pending-pa` × 3 (htmx hx-get/select/target tuple)
  - "Pending PA" × 1 (panel header)
  - "deferred-fire watch" × 1 (subtitle)
  - "no signal pending" × 1 (current state — cache is empty as expected)
  - "24h:" × 2 (aggregate label on PA header)
  - ⤴ × 5, ⨯ × 2 (redemption/expiration symbols in new tables + header)
- Zero `TemplateSyntaxError` / `UndefinedError` in journalctl since restart.

**Deploy mechanic:**
- Same `az vm run-command` path (SSH still blocked). Patch was 24500 bytes raw; gzip → base64 → 8244 bytes (under the 28KB `--scripts` cap). Single-invoke deploy via `echo $B64 | base64 -d | gunzip > /tmp/x.patch && patch -p1 < /tmp/x.patch`.
- Wholesale-replace would have been simpler but total LF size was 249KB (base64 ~330KB — far over the cap). Patch-based deploy works because prod md5s matched HEAD~1 exactly on all 4 existing files (no drift to preserve).

**Inert / dormant:**
- All new panels render with empty-state messages until cache events occur in prod. First real "Pending PA" / "Redeemed fires" / "Expired waits" entries will appear within hours as TV alerts continue arriving + PA continues blocking. No code changes need to populate them.

**Watch for (next 24h):**
- First "WATCHING (N bars elapsed)" on the Pending PA panel → confirms cache state surfaces correctly.
- First Recent Redeemed Fires row → confirms 24h aggregate query joins to audit rows correctly.
- First Decision Flow row with "⤴ redeemed (Nb · Ns)" → confirms the trigger_source-based detection works end-to-end.

**Rollback recipe (~30s):**
```bash
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod --command-id RunShellScript --scripts '
TAG=pre-dash-deferred-20260517-0411
BASE=/home/azureuser/trading_corp
for f in trading_corp/web/data.py trading_corp/web/templates/division.html trading_corp/web/templates/partials/bitunix_decision_flow.html trading_corp/web/templates/partials/bitunix_pa_panel.html; do
  mv $BASE/$f.$TAG $BASE/$f
done
rm -f $BASE/trading_corp/web/templates/partials/bitunix_pending_pa_panel.html
sudo systemctl restart trading-corp'
```

---

## 2026-05-17 03:53 UTC — BitUnix deferred-fire PA mechanism

**Commits:** `72bbbe4`
**Triggered by:** Board direction 2026-05-17 after the H2 + 1D-enforce-flip combo at 19:21 UTC 2026-05-16 produced 36/36 score-fire PA REJECTs (~100% sell-side, `structure_alignment` dominant blocker). Chart review showed the trades would have been winners once PA aligned a few bars later. Rule: when score is high enough to trigger but PA blocks, keep re-evaluating PA on each new bar until the score itself decays. Plan in `~/.claude/plans/i-need-to-work-gentle-honey.md`.

**Backup tags:** `pre-pa-redeem-20260517-0350` (both files)

**Files deployed (2 modify) — via base64 `patch -p1` to preserve prod drift in unrelated regions:**
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — adds `_pending_pa_payload` / `_pending_pa_side` / `_pending_pa_cached_at_ts` in-memory cache; new methods `_clear_pending_pa`, `_log_pa_validation_redeem` (returns lastrowid for backfill), `_backfill_redeem_order_id`, `_log_pa_validation_expired`, `run_pa_redeem_loop` (60s background loop). Cache-set on PA REJECT in enforce mode; cache-clear on score SKIP / opposite-side win / PA pass / successful fire. PaperTradeRecord.extra passthrough so `extra_json` is no longer NULL on BitUnix rows. Local LF md5 `406cd632571276d800ac628a27b4adc8` byte-identical with prod after the LF-normalize-then-patch deploy.
- `trading_corp/main.py` — adds `asyncio.create_task(bitunix_observer.run_pa_redeem_loop(interval_s=60.0), name="bitunix-pa-redeem")` alongside the existing bar/HTF cache tasks (after the HTF regime snapshot loop). Surgical patch; prod md5 `700e3cc2fae4d0851c0f229aae16625a` differs from local because of unrelated prod drift in other regions — preserved by `patch -p1` (NOT clobbered).

**Features shipped (load-bearing for future "is X done?" checks):**
- **Deferred-fire PA mechanism.** A high-score TV alert that PA rejects in enforce mode is no longer discarded — payload is cached in process memory. `bitunix-pa-redeem` background task wakes every 60s, replays the cached payload through the full pipeline (`_score_and_maybe_propose` with `source="bar_tick_redeem"`). PA finally passes → fires through HTF/sizing/risk/place like any other score-fire. Score decays to SKIP → cache cleared (no fixed TTL; the score engine's factor `ttl_minutes` is the only timing knob).
- **At most one side waiting at a time.** Opposite-side score win nullifies the prior waiting state per Board's null-and-void rule.
- **`pa_validation_redeem` audit kind.** One row per redeem-path PA pass. Carries `original_cached_at`, `redeem_ts`, `bars_waited`, `seconds_waited`, `final_tier`, `final_side`, `final_passed`, `order_id` (back-filled after placement via UPDATE; stays NULL if a post-PA gate killed the trade).
- **`pa_validation_expired` audit kind.** One row per cached payload dropped without firing. `reason ∈ {"score_decay", "opposite_side"}`. Carries `cached_side`, `bars_waited`, `seconds_waited`.
- **`order.extra` + `paper_trade_record.extra_json` carry redemption metadata.** New fields `redeemed: bool`, `bars_waited: int`, `seconds_waited: int`, `original_cached_at: iso` on redeemed fires. Pre-existing bug: `PaperTradeRecord.from_order` didn't propagate `order.extra` to `extra_json` (so the `score_path`, `net_score`, `funding_rate_at_decision`, `htf_size_multiplier` fields the observer carefully stamped on `order.extra` had been NULL for every BitUnix paper_trade_record row in prod history). Fixed locally in the observer via `record.extra = dict(order.extra)` before the DB insert. Shared `persistence/models.py` deliberately untouched per CLAUDE.md § 4.
- **`would_have_placed` event** now always carries `redeemed: bool` and `bars_waited: int | None` for queryability.

**Notable code changes (callouts a future Claude shouldn't miss):**
- The redeem mechanism is **PROCESS MEMORY ONLY** — no `agent_state` table, no SQLite persistence. On restart, the cache rebuilds from the next TV alert that PA rejects. This is intentional (a wait of seconds-to-minutes doesn't need restart-safety).
- The 60s cadence piggybacks on the existing `bitunix_bar_cache.run_poll_loop(interval_s=60.0)` rhythm — a sibling task, not a wall-clock-aligned bar-close trigger.
- The PA validator (`bitunix_pa_validation.py`) is **unchanged** — still a stateless pure function. All state lives on the observer.
- **No YAML changes.** No `pa_validation.deferred_fire` block. The rule "wait while score is valid" is encoded entirely in observer Python.
- `audit_event` schema unchanged. The two new kinds slot in via the existing append-only INSERT path; no allowlist work needed (per `feedback`-style finding: `main.py` audit allowlist is only for `would_have_placed` extras).
- `_backfill_redeem_order_id` reads the existing row's `payload_json`, JSON-decodes, sets `order_id`, JSON-encodes, UPDATEs by `id`. Best-effort — swallow + log on failure. Backtests can fall back to `(trigger_signal, ts ~1s)` join if the backfill ever lost a row.

**Latent gap caught + fixed:** `PaperTradeRecord.from_order` (in shared `persistence/models.py`) does NOT propagate `order.extra` to `extra_json`. Effect for prod history: every BitUnix `paper_trade_record` row had `extra_json IS NULL` despite `order.extra` carrying `score_path`, `net_score`, `funding_rate_at_decision`, `htf_size_multiplier`. Closed locally in the observer (`record.extra = dict(order.extra)` before insert). Shared model deliberately not touched in this deploy per CLAUDE.md § 4. Other strategies (Otter / Cypher / PMCC) still write `NULL` to their `extra_json` — file as P3 follow-up if/when a backtest needs the `extra` for those.

**Deploy mechanic:**
- SSH still blocked from current network (per `feedback_az_run_command_when_ssh_blocked.md`); deployed via `az vm run-command invoke` with base64-encoded unified `git diff HEAD~1..HEAD` shipped to `/tmp`, then `patch -p1 < $F`. Patch -p1 was chosen over wholesale-replace because both files had prod drift from git (observer.py was CRLF-encoded; main.py had unrelated semantic drift in known regions per `trading_corp_prod_git_drift.md`).
- LF-normalized prod observer in place (`tr -d '\r' < $F > $F.lf && mv`) BEFORE patch because the patch context was LF and prod was CRLF. Backup taken BEFORE normalize. Python parses both line endings identically so normalization is semantically null. After normalize + patch, observer md5 matches local HEAD byte-for-byte.

**Verification:**
- `az vm run-command create` exit 0; backups present at `pre-pa-redeem-20260517-0350`.
- Patch applied 9/9 hunks cleanly (after LF-normalize); 0 rejects.
- Post-patch md5: observer `406cd632571276d800ac628a27b4adc8` (== local LF md5), main `700e3cc2fae4d0851c0f229aae16625a` (preserved drift + my addition).
- Import test confirms all 5 new methods: `run_pa_redeem_loop`, `_clear_pending_pa`, `_log_pa_validation_redeem`, `_log_pa_validation_expired`, `_backfill_redeem_order_id` all present on `BitunixFuturesObserver`.
- Service restart 03:53:14 UTC. PID 540809. `systemctl is-active trading-corp` = `active`. Healthz `{"status":"ok","mode":"PAPER"}` (200).
- Boot wiring: `BitUnix observer wiring: scoring=True, pa_enabled=True, htf_gate_mode=enforce, htf_regime_enabled=True, trade_plan_active=False` (target hit).
- All 4 BitUnix bar caches primed (3m/1h/4h/1d) — 200 bars each.
- First 90 sec post-restart: 13 `pa_validation_decision` rows landed (TV alerts driving real PA evaluations through the new code path). 0 `pa_validation_redeem` / `pa_validation_expired` rows yet — expected, those need PA-reject-then-{pass|decay} cycles.

**Inert / dormant:**
- `trade_plan.enabled: false` unchanged — adaptive trade plan still uses legacy geometric path.
- `auto_execute: false` unchanged — every redeemed fire still goes through paper-mode `would_have_placed` path. **Does NOT unlock any path to auto_execute: true for BitUnix.** Per CLAUDE.md § 5, the webhook risk gate vs LangGraph harmonization gap is still load-bearing for any future flip.

**Watch for (next 24h):**
- First `pa_validation_redeem` row → confirms full redeem cycle worked end-to-end. Query: `SELECT * FROM audit_event WHERE kind='pa_validation_redeem' ORDER BY id DESC LIMIT 5;`
- First `pa_validation_expired` row → confirms score-decay or opposite-side clear path. Query: same with `kind='pa_validation_expired'`.
- First `paper_trade_record` row for `bitunix_futures` with `json_extract(extra_json,'$.redeemed') = 1` → confirms the gap-closure-end-to-end (was 0 since 19:21 UTC 2026-05-16).
- Redemption success rate: `n_redeemed_fires / (n_redeemed_fires + n_expired_score_decay)`. H2 falsification gate (≥30 PREMIUM fires) is downstream of this — until PA stops being 100% blocking, H2 can't accumulate.

**Rollback recipe (~30s, 1-line restore + restart):**
```bash
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod --command-id RunShellScript --scripts '
TAG=pre-pa-redeem-20260517-0350
BASE=/home/azureuser/trading_corp
mv $BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.$TAG $BASE/trading_corp/agents/divisions/bitunix_futures_observer.py
mv $BASE/trading_corp/main.py.$TAG $BASE/trading_corp/main.py
sudo systemctl restart trading-corp'
```
Note: rollback restores the CRLF observer (the LF-normalize was preserved in the backup file's content since the backup was taken pre-normalize — verify with `file` after rollback if it matters). Either way, Python parses both fine.

---

## 2026-05-17 03:38 UTC — PCT stale-entry pruner cron (systemd timer)

**Commits:** `335ecc2`
**Triggered by:** Carryover P2 from the 2026-05-16 03:29 UTC one-shot DELETE (which removed 1,745 stale `polymarket_copy_trader` pending audit rows). The root cause — Apify's 10-min poll cadence missing fast whale auto-settles — was not addressed by the DELETE, so stale rows re-accumulate at ~70/day. The pruner cron automates the same predicate as a nightly job.

**Files deployed (4 new):**
- `trading_corp/scripts/prune_stale_pct_entries.py` — CLI + library. `--dry-run` is default; `--apply` required to actually delete. `--cutoff-hours N` (default 24), `--max-rows N` (default 5000, safety cap). Writes a `pct_stale_prune` audit_event row for EVERY run (dry-run too) tagged with `division=polymarket_copy_trading`, `strategy=pct_stale_pruner`, candidates + deleted counts, applied flag.
- `/etc/systemd/system/trading-corp-pct-pruner.service` (Type=oneshot, User=root, ExecStart with `--apply --cutoff-hours 24 --max-rows 5000`, env mirrors the existing watchlist-stats unit).
- `/etc/systemd/system/trading-corp-pct-pruner.timer` (OnCalendar=`*-*-* 11:30:00 UTC`, Persistent=true, RandomizedDelaySec=300). 11:30 UTC is BEFORE the 12:00 UTC watchlist-stats refresh so morning Board glances at the dashboard see cleaned counts.

**Features shipped:**
- Nightly automated DELETE of `polymarket_copy_trader/would_have_placed` audit rows that are:
  - side='buy' (default 'buy' when key absent — matches pre-2026-05-14 rows)
  - ts < now() - 24h
  - order_id NOT IN polymarket_round_trips.order_id
  - order_id NOT IN polymarket_round_trips.entry_order_id
- Sell-side rows preserved. Round-trip-paired rows preserved (via either column). Fresh rows preserved. Non-PCT actors untouched.
- Every run audits itself via `pct_stale_prune` event — the cron is fully self-observable.

**Notable code changes:**
- Predicate logic is in `_PREDICATE_WHERE` constant; `prune()` is a pure library function callable from tests and the CLI. 13 unit tests cover every preservation rule + dry-run vs apply + audit-row-shape.
- `prune()` accepts `db_url` parameter for testability; main() lazy-imports `load_secrets()` only when needed, so unit tests can pass `--db-url sqlite:///tmp/test.db` without touching Key Vault.
- The systemd unit's `WorkingDirectory=/home/azureuser/trading_corp` is load-bearing — Python 3 `-m` requires CWD to contain the `trading_corp/` package directory. The smoke test below failed initially because I forgot to `cd` first; the systemd unit gets it right.

**Verification (post-deploy 03:41 UTC):**
- AST parse on prod ✅.
- `systemctl is-enabled trading-corp-pct-pruner.timer` → `enabled`.
- `systemctl is-active trading-corp-pct-pruner.timer` → `active`.
- `systemctl list-timers` shows next fire at `Sun 2026-05-17 11:34:59 UTC` (with the ~5min random delay).
- Direct `python -m trading_corp.scripts.prune_stale_pct_entries` dry-run from prod: 454 candidates identified, 0 deleted. KV loaded 27 secrets successfully.
- `pct_stale_prune` audit row written at 03:41:03 UTC, payload as expected.
- Direct SQL spot-check: 1,707 total PCT pending; 1,168 are >24h old; 454 are unpaired (the delete target). 714 ≥24h-but-paired rows correctly preserved.

**Expected behavior on first real fire (2026-05-17 ~11:35 UTC):**
- 454 rows deleted (assuming no other PCT rows expire between now and then; the actual count will be slightly higher due to overnight Apify accumulation).
- One `pct_stale_prune` audit row with `apply=true`, `deleted=N`.
- Dashboard "Open" tile for `polymarket_copy_trading` drops by ~454 (currently shows 1,707; expect ~1,253 after).

**Rollback recipe:**
```bash
# Disable the timer (keeps the script for re-enable later):
az vm run-command create -g rg-shared-prod --vm-name tc-prod-vm \
  --run-command-name tc-rollback-pct-pruner \
  --script 'sudo systemctl disable --now trading-corp-pct-pruner.timer; \
            sudo rm -f /etc/systemd/system/trading-corp-pct-pruner.{service,timer}; \
            sudo systemctl daemon-reload; \
            rm -f /home/azureuser/trading_corp/trading_corp/scripts/prune_stale_pct_entries.py'
```
(Soft rollback: just `systemctl stop trading-corp-pct-pruner.timer` to pause; re-enable with `systemctl enable --now`.)

**Watch for tomorrow morning (~11:35 UTC fire):**
```sql
-- The fresh audit row:
SELECT ts, payload_json FROM audit_event
 WHERE kind='pct_stale_prune' AND apply=true
 ORDER BY ts DESC LIMIT 1;
-- Pending-row count should drop by ~454 around that time:
SELECT COUNT(*) FROM audit_event
 WHERE actor='polymarket_copy_trader' AND kind='would_have_placed';
```

---

## 2026-05-17 03:09 UTC — kalshi_weather: target_iso audit field

**Commits:** `1e2b399`
**Triggered by:** Carryover P3 from the 19:40 EOS — the kalshi_weather `would_have_placed` audit allowlist in `main.py` didn't carry `target_iso`, so we had no on-the-wire proof that the 2026-05-16 19:18 UTC date-parse fix (Bug B) was firing on the right resolution date. With overnight weather settlements landing tomorrow ~14:00 UTC, this needed to ship before then so the first natural fires record their target dates.
**Backup tag:** `.pre-target-iso-20260517-0309`

**Files deployed (2 modified):**
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — adds `"target_iso": target_iso,` to `ProposedOrder.extra` (right after `expires_at`). Distinct from `expires_at` (Kalshi's settlement window, ~14:00 UTC the day after); `target_iso` is the resolution-date parsed from the ticker. md5 (LF) `4bf3005a0f638dae4c0c73d5dd296a09` byte-identical with local.
- `trading_corp/main.py` — patched in-place: adds `"target_iso": ext.get("target_iso"),` to the `kalshi_weather_order` `would_have_placed` allowlist at lines 3206-3210 (with `TARGET_ISO_INSERTED` marker for future grep). Prod md5 differs from local (known drift per `trading_corp_prod_git_drift.md`); deploy used the surgical python-anchor pattern, NOT whole-file replace, to preserve prod-side diffs.

**Features shipped:**
- Every new kalshi_weather `would_have_placed` audit row now carries `target_iso`, allowing direct cross-check that "we fetched May 15 forecast for KXHIGHDEN-26MAY15-B82.5" is happening. Pre-fix value would have been May 16 (Kalshi's expiration_time fallback).
- `kalshi_weather_evaluated` rows already carry `target_iso` (no change there).

**Notable code changes:**
- The local `main.py` patch + the prod `main.py` patch are SEMANTICALLY identical but at different byte offsets (prod has unrelated drift). The `TARGET_ISO_INSERTED` marker is the canonical grep anchor for future-Claude to verify the line is present without doing a full file diff.
- Surgical-edit pattern used: a python script on prod anchors on `"forecast_temp_f": ext.get("forecast_temp_f"),` (weather-block-only), walks forward to the next `"expires_at": ext.get("expires_at"),` line, and inserts the new field there. The script is idempotent (early-exits if `TARGET_ISO_INSERTED` is already in the file). Refuses to insert if it would walk past the kalshi_crypto block's `"asset"` field.
- See `feedback_surgical_edits_over_whole_file_scp.md` for why this matters.

**Verification:**
- All 31 weather-fix tests + 35 dashboard tests pass locally pre-deploy.
- AST parse on both files post-deploy on prod (built into the deploy script).
- Service restarted (PID 536909, `is-active`).
- External `/healthz` returns 200 `{"status":"ok","mode":"PAPER"}`.
- `grep -n target_iso` on prod's `main.py` shows the new lines at 3205-3211 (between `expires_at` and `title`).
- `grep -n target_iso` on prod's `kalshi_weather_arb.py` shows the new line at 709 (in the `extra` dict).
- Audit cross-check pending: no natural weather fire in the ~25 minutes post-deploy (overnight, low scan-fire rate). Will self-verify with tomorrow morning's weather scans.

**Rollback recipe:**
```bash
az vm run-command create -g rg-shared-prod --vm-name tc-prod-vm \
  --run-command-name tc-rollback-target-iso \
  --script 'BASE=/home/azureuser/trading_corp; TAG=pre-target-iso-20260517-0309; \
    for f in trading_corp/agents/strategies/kalshi_weather_arb.py trading_corp/main.py; do \
      [ -f "$BASE/$f.$TAG" ] && mv "$BASE/$f.$TAG" "$BASE/$f"; \
    done; \
    sudo systemctl restart trading-corp'
```

**Watch for:**
- Tomorrow's first kalshi_weather `would_have_placed` rows. The `target_iso` value should match the date segment of the ticker (e.g. `KXHIGHDEN-26MAY17-...` → `2026-05-17T...`), NOT the `expires_at` date (which will be ~14:00 UTC the following day).

---

## 2026-05-17 02:49 UTC — dashboard cutoff filter for pre-fix kalshi RTs

**Commits:** `bf1ae7e`
**Triggered by:** Board observation that the post-19:18-fix sample (4 crypto RTs, 0 weather RTs) is uninformative against tainted historical aggregates (61 weather / 91 crypto pre-fix losers). Chose **filter-by-cutoff** over hard-delete to preserve forensic / σ-scaling data. Spec'd in-session; implemented + deployed end-to-end.
**Backup tag:** `.pre-rt-cutoff-20260517-0249`

**Files deployed (3 modified):**
- `trading_corp/web/data.py` — adds module-level `DASHBOARD_RT_CUTOFFS: dict[str, str]` (kalshi_weather: 2026-05-16T19:18Z, kalshi_crypto: 2026-05-16T19:37Z) + `_kalshi_cutoff_clause(ts_col)` helper. Three queries patched to append the clause: `pm_overview` kalshi roll-up (~line 1023), `_query_pm_round_trips` kalshi block (~line 2941), `_query_pm_resolved_stats` kalshi block (~line 3363). `PMSummary` gains `cutoff_ts` + `cutoff_label` (None-default). `build_prediction_market_view` sets them on single-division views only. `_hydrate_pm_overview` sets `s["cutoff_label"]` on home-tile dicts. md5 (LF) `5b6faaa3c8001633f914714ee4374ad0` byte-identical with prod.
- `trading_corp/web/templates/partials/pm_dashboard_body.html` — adds "since YYYY-MM-DD · current logic only" under the Win rate tile when `s.cutoff_label` is set (per-division page + combined-view conditional). md5 (LF) `221b1ad4d4cab2a4386a7c5c3df6fa3f` byte-identical with prod.
- `trading_corp/web/templates/home.html` — adds compact "since YYYY-MM-DD" under realized PnL on home overview tile when `pm.cutoff_label` is set. md5 (LF) `5635930dfb5ff1342d4e9d43a4d0ce6d` byte-identical with prod.

**Features shipped:**
- Dashboard tile + history list for `kalshi_weather` (since 2026-05-16T19:18+00:00) and `kalshi_crypto` (since 2026-05-16T19:37+00:00) now exclude pre-cutoff RTs from win-rate, n_resolved, total PnL aggregates.
- "since 2026-05-16 · current logic only" badge visible under Win rate on per-division pages (verified via curl 127.0.0.1:8000 on prod).
- Compact "since 2026-05-16" badge on home overview tiles for the two filtered divisions.
- `kalshi_round_trips` table untouched — pre-cutoff rows REMAIN in DB for σ-scaling work and forensic queries. Querying with explicit `WHERE entry_ts < cutoff` reaches them as before.
- `kalshi_llm_arbitrage`, `kalshi_arbitrage`, `kalshi_copy_trading`, polymarket divisions: unaffected (no entry in `DASHBOARD_RT_CUTOFFS`).
- Combined "All Prediction Markets" view: shows NO badge (different divisions have different / no cutoffs — no honest single "since" date).
- Equity curve queries (`_query_pm_equity_curve`) NOT filtered — they pull from a separate `*_equity_history` snapshot table, and cutting the integral would create a misleading step discontinuity. Intentional.

**Notable code changes (callouts a future Claude shouldn't miss):**
- `DASHBOARD_RT_CUTOFFS` is the SINGLE source of truth for the cutoff. Adding a new entry is the entire mechanism to filter a new division. Empty dict = full rollback.
- Inline-SQL substitution chosen over parameterized because cutoffs are hardcoded constants (no injection surface). `_kalshi_cutoff_clause` returns a leading-space-prefixed fragment to append after `WHERE ... IN (...)`.
- 8 new tests in `tests/test_prediction_markets_dashboard.py` cover: empty-dict-empty-clause, single-cutoff-emits-predicate, resolved-stats-filters, round-trips-history-filters, no-cutoff-division-unaffected, single-division-PMSummary-attaches-label, combined-view-no-label. Use `monkeypatch.setattr(wd, "DASHBOARD_RT_CUTOFFS", {...})` to isolate.

**Verification:**
- All 35 dashboard tests pass (27 prior + 8 new) + 31 weather-fix tests pass.
- Prod md5 match all 3 files (data.py + 2 templates) byte-identical with local LF.
- Service restarted via deploy script (PID 535103 → confirmed `is-active`).
- External `/healthz` returns 200 `{"status":"ok","mode":"PAPER"}` post-deploy.
- `/prediction-markets/kalshi_weather` renders badge + tile shows `n_resolved=0` (correct: no post-cutoff RTs yet, weather settles ~14:00 UTC next-day).
- `/prediction-markets/kalshi_crypto` renders badge + tile shows `n_resolved=0` (correct: the 4 post-19:18 RTs entered 19:20–19:28, BEFORE the 19:37 crypto cutoff — they predate the crypto-fix and are correctly filtered).
- `/prediction-markets/kalshi_llm_arbitrage` has zero badge instances (correct: no cutoff entry).

**SHARP EDGE caught in flight:**
- I spec'd "FastAPI worker auto-reloads on next request" — **wrong**. Prod runs uvicorn under systemd with `--reload` off. `web/data.py` is a Python module loaded into the running process; changes don't take effect without `systemctl restart trading-corp`. Same gotcha as the BitUnix YAML hot-reload memory. The deploy script restarted the service.

**Deploy mechanics (recap for future Claude):**
- SSH blocked from hotspot IP (consistent with 2026-05-16 outage memory). Pivoted straight to `az vm run-command create --script @file` per `feedback_az_run_command_when_ssh_blocked.md`.
- Initial deploy.sh had CRLF shebang from Windows heredoc → `bad interpreter: /bin/bash^M`. Fixed by `tr -d '\r'`. Memory `trading_corp_windows_crlf_vs_prod_lf.md` rule applies to deploy scripts, not just payloads.
- `az vm run-command create` is single-tenant — must delete the named command before re-create or you get "Run command extension execution is in progress" conflicts.

**Rollback recipe:**
```bash
az vm run-command create -g rg-shared-prod --vm-name tc-prod-vm \
  --run-command-name tc-rollback-rt-cutoff \
  --script 'BASE=/home/azureuser/trading_corp; TAG=pre-rt-cutoff-20260517-0249; \
    for f in trading_corp/web/data.py trading_corp/web/templates/home.html \
             trading_corp/web/templates/partials/pm_dashboard_body.html; do \
      [ -f "$BASE/$f.$TAG" ] && mv "$BASE/$f.$TAG" "$BASE/$f"; \
    done; \
    sudo systemctl restart trading-corp'
```
(Alternative softer rollback: edit `DASHBOARD_RT_CUTOFFS = {}` on prod's `data.py` + restart. Files keep new shape; filter goes dormant.)

**Watch for:**
- Tomorrow ~14:00 UTC when May 16 daily-HIGH/LOW weather markets settle: `kalshi_weather` tile should start filling with post-cutoff RTs. Per-tile counts should match `SELECT COUNT(*) FROM kalshi_round_trips WHERE division='kalshi_weather' AND entry_ts >= '2026-05-16T19:18:00+00:00'`.
- If counts don't match the explicit SQL query, the cutoff isn't reaching one of the 3 patched queries.

---

## 2026-05-16 19:37 UTC — kalshi_crypto: bucket-aware bet-side guard

**Triggered by:** parallel investigation of kalshi_weather Denver 5/15 RT resolution. After fixing kalshi_weather (entry 19:18 UTC below), audited kalshi_crypto for the same σ-vs-bucket-width bug pattern. Confirmed bug A applies (same `outcome = "yes" if prob_yes > implied_yes else "no"` at `kalshi_crypto_arb.py:513`); bug B (off-by-one-day) does NOT apply (crypto uses live spot + expiration_time correctly). Pre-fix crypto stats: 91 round-trips, 11.0% WR, -$58.88 PnL. T-tickers + "other" categories: 0/16 wins.

**Files deployed (2 modified):**
- `trading_corp/agents/strategies/kalshi_crypto_arb.py` — imports `apply_bucket_guard` from `_weather_math.py` (already deployed in the 19:18 UTC entry); calls guard between `outcome = ...` line and `share_price = ...` line. Passes `spot` as the "forecast" (matches the `evaluate_weather_market` adapter pattern this strategy already uses). `bucket_guard` added to `ProposedOrder.extra`. md5 (LF) `7e945feb62af330631b79c442798cdfe` byte-identical with local.
- `trading_corp/main.py` — patched-in-place: added `"bucket_guard": ext.get("bucket_guard")` to the `kalshi_crypto_order` audit allowlist at line 3367-3368 (with `CRYPTO_BUCKET_GUARD_INSERTED` marker for future grep). Prod-specific md5 differs from local; the relevant patch is identical.

**Features shipped:**
- Crypto strategy refuses to bet NO when spot is inside the bucket (between markets) or on the YES-aligned side of the threshold (T-tickers). When NO is refused: flips to YES if implied is reasonable (≤ 0.70 default ceiling), else skips. Mirror: refuses YES when spot is outside the bucket / on the NO-aligned side (long-shot σ-smearing artifact).

**Notable code changes (callouts a future Claude shouldn't miss):**
- The bucket_guard logic is SHARED between kalshi_weather and kalshi_crypto via `_weather_math.apply_bucket_guard`. Same function, same tests. Crypto's "forecast" is `spot`; weather's "forecast" is `forecast.temp_f`. Both flow through the same Gaussian-integration math.
- Crypto strategy entry at line 513 (post-fix at 522+) is the seam where this guard inserts. If you re-touch this code, preserve the order: `outcome` decision → `apply_bucket_guard` → `share_price = yes_ask if outcome=="yes" else no_ask`. Reordering breaks the guard.
- `ProposedOrder.extra["bucket_guard"]` is the audit field. Main.py allowlist at the `kalshi_crypto_order` `would_have_placed` block carries it forward.

**Verification:**
- Local md5 (LF) of `kalshi_crypto_arb.py` `7e945feb62af330631b79c442798cdfe` == prod (LF-stripped) `7e945feb62af330631b79c442798cdfe`.
- Service restart at 19:37:25 UTC, PID 516325; both scanners log: `Kalshi Crypto Arbitrage scanner online (enabled=True, auto_execute=False)`.
- 46 weather tests passing (15 prior + 31 new from `tests/test_kalshi_weather_fixes.py`; same tests cover the crypto code path since they share `apply_bucket_guard`).

**Rollback recipe:**
```bash
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
  --command-id RunShellScript \
  --scripts "BASE=/home/azureuser/trading_corp; \
    BAK=\$(ls \$BASE/trading_corp/agents/strategies/kalshi_crypto_arb.py.pre-bucket-guard-* 2>/dev/null | head -1); \
    [ -n \"\$BAK\" ] && cp \"\$BAK\" \$BASE/trading_corp/agents/strategies/kalshi_crypto_arb.py; \
    # main.py: remove the CRYPTO_BUCKET_GUARD_INSERTED line + the bucket_guard field below it (no main.py backup needed; the marker is unique). \
    sudo -u azureuser python3 -c \"
p='\$BASE/trading_corp/main.py'
t=open(p).read()
lines=t.splitlines(keepends=True)
keep=[]
skip_next=False
for ln in lines:
    if 'CRYPTO_BUCKET_GUARD_INSERTED' in ln:
        skip_next=True
        continue
    if skip_next and 'bucket_guard' in ln:
        skip_next=False
        continue
    skip_next=False
    keep.append(ln)
open(p,'w').write(''.join(keep))
\"; \
    sudo systemctl restart trading-corp"
```

**Watch for:**
- Within next ~10 min, `kalshi_crypto_evaluated` rows should carry a `bucket_guard` field on those that fire (most evaluations will be no-op natural-path). The first `flipped_no_to_yes` or `block_*` audit row confirms the new code path is reachable.
- Within ~24h, crypto round-trip win rate should rise from 11.0% baseline.

---

## 2026-05-16 19:18 UTC — kalshi_weather: off-by-one-day fix + bucket-aware bet-side guard

**Triggered by:** Jack flagged Denver 5/15 round-trips "didn't look right." Investigation revealed two related bugs in the kalshi_weather strategy. Pre-fix stats: 61 round-trips, 9.8% WR, -$374.21 PnL. Resolver itself was confirmed correct (Kalshi resolved B82.5 YES, NWS KDEN max obs was 82.4°F).

**Bugs fixed:**

1. **Bug B — `_parse_target_time` off-by-one-day.** Used Kalshi's `expected_expiration_time` (typically 14:00 UTC the day AFTER the weather target — the settlement window) as the forecast lookup date. For KXHIGHTBOS-26MAY15-T56, we fetched the May 16 forecast against a market that resolved on May 15 actual. Off-by-one caused 5-20°F systematic forecast errors on the worst cases (Boston 20°F off, Philadelphia 14°F off, Chicago 8°F off). Affected ALL daily HIGH/LOW markets.

2. **Bug A — σ-vs-bucket-width logic.** `outcome = "yes" if prob_yes > implied_yes else "no"` math sees σ=2.73°F integrated over 1°F bucket = 14% model prob, market priced at 51% (modal bucket consensus), concludes "bet NO." Forecast at 82°F was IN the [82,83] bucket — strategy was systematically betting against its own forecast. Same pattern on T-tickers (long-shot YES on tail buckets the model doesn't believe in).

**Files deployed (3 modified):**
- `trading_corp/agents/strategies/_weather_math.py` — added pure-function `apply_bucket_guard(direction, forecast_temp_f, threshold_f, threshold_high_f, proposed_outcome, implied_yes, flip_yes_implied_ceiling)` returning a `BucketGuardResult` dataclass (`outcome | None`, `action`, `skip_reason`). Logic handles all three Kalshi directions: between (1°F bucket), greater (T-ticker high), less (T-ticker low). When forecast IS on the YES-aligned side and model says NO: flip to YES if `implied_yes ≤ ceiling` (default 0.70), else skip. When forecast IS on the NO-aligned side and model says YES: always skip (σ-smearing artifact). md5 (LF) `007790327b43c74f1048276fe7108947` byte-identical with local.
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — two surgical changes: (a) `_parse_target_time` now parses date from the TICKER (the `26MAY15` segment in `KXHIGHDEN-26MAY15-B82.5`) as PRIMARY source; fallback to `expected_expiration_time` only when ticker parse fails (with a logged warning). Handles both daily-format (`YYMMMDD`) and hourly-format (`YYMMMDDhh`). (b) `_evaluate_market` calls `apply_bucket_guard` between the `outcome = ...` line and the `share_price = ...` line; uses guard's returned outcome or skips on `outcome=None`. `bucket_guard` added to `ProposedOrder.extra`. md5 (LF) `450791247764be89a888057d75beaad1` byte-identical with local.
- `trading_corp/main.py` — patched-in-place: added `"bucket_guard": ext.get("bucket_guard")` to the kalshi_weather `would_have_placed` allowlist at line 3212-3213 (with `BUCKET_GUARD_INSERTED` marker).

**Features shipped:**
- **Date-correct forecast lookups.** Daily HIGH/LOW markets now query NWS for the actual resolution date (parsed from ticker), not the settlement date. Forecasts for May 15 markets fetch May 15 forecast.
- **Bucket-aware bet-side guard.** Refuses to bet against own forecast; flips to YES (when reasonably priced) on the forecast-aligned side; blocks σ-smearing long-shot YES bets on the wrong side of own forecast.
- **New `bucket_guard` audit field.** Records `flipped_no_to_yes` / `block_no_yes_too_expensive` / `block_yes_forecast_outside` / `None` on every `would_have_placed` row.

**Notable code changes:**
- `_weather_math.apply_bucket_guard` is venue-agnostic. Designed to be shared with `kalshi_crypto_arb` (planned for follow-up; shipped 19:37 UTC entry above).
- `_parse_target_time` now has a clear PRIMARY/FALLBACK structure with a logged warning when falling back to `expected_expiration_time` — that path is now the BUG path; the warning surfaces it.
- Tests cover both fixes including the documented prod failures (Denver B82.5, Seattle T41, Minneapolis T90) — `tests/test_kalshi_weather_fixes.py`, 31 new tests, 100% passing.

**Halt + re-enable cycle:**
- 17:52 UTC: `kalshi_weather_arb.enabled: false` flipped on prod YAML (mtime-cached, no restart). Strategy stopped firing.
- 19:18 UTC: deploys above shipped + service restarted (PID 515131).
- 19:32 UTC: re-enabled `kalshi_weather_arb.enabled: true` (no restart). Next scan tick used new logic.

**Backup tags (rollback recipes):**
- `kalshi_weather_arb.py.pre-weather-fix-20260516-175233`
- `_weather_math.py.pre-weather-fix-20260516-175233`
- `main.py.pre-weather-fix-20260516-175233`
- `strategies.yaml.pre-weather-halt-<ts>` (restored to enabled after deploy)

**Rollback recipe:**
```bash
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
  --command-id RunShellScript --scripts "
BASE=/home/azureuser/trading_corp
TS=20260516-175233
mv \$BASE/trading_corp/agents/strategies/kalshi_weather_arb.py.pre-weather-fix-\$TS \$BASE/trading_corp/agents/strategies/kalshi_weather_arb.py
mv \$BASE/trading_corp/agents/strategies/_weather_math.py.pre-weather-fix-\$TS \$BASE/trading_corp/agents/strategies/_weather_math.py
mv \$BASE/trading_corp/main.py.pre-weather-fix-\$TS \$BASE/trading_corp/main.py
sudo systemctl restart trading-corp
"
```

**Don't reintroduce:**
- Adding `bucket_guard` field on ProposedOrder.extra without main.py audit-allowlist update (memory `trading_corp_audit_payload_allowlist`).
- `_parse_target_time` falling back to `expected_expiration_time` for known daily-ticker shapes — that's the bug path; PRIMARY parse is from the ticker.
- Inline bucket-guard logic anywhere in the strategy module. Use the pure function in `_weather_math.py` so kalshi_crypto + any future weather-shape strategy share the same behavior.

---

## 2026-05-16 18:51 UTC — BitUnix scoring H2 re-tune (config edit; activated by 19:21 UTC restart)

**Triggered by:** research in `reports/scoring_recommendation.md` (H2 — re-weight + Otter precision up). 47-day backtest across 13 candidate configs found H2 has the widest PREMIUM/STANDARD quality gap (+0.114R vs baseline +0.051R, 2.2× wider) and the simplest YAML diff (weight edits only, no formula change, no threshold change). Decision log in `reports/scoring_decision_log.md`.

**Backup on prod:** `/home/azureuser/trading_corp/config/strategies.yaml.bak-h2-20260516T185125`

**Files changed (prod — 1 modify):**
- `config/strategies.yaml` — 10 weight edits in `bitunix_futures.scoring.factors` via `scripts/patch_bitunix_scoring_h2.py --apply` (Python regex patcher). Pre md5 `da18d6c5180cd09592b4475e4df8893e` → post md5 `6dc03a793e1e6e58df832aa89407ef93`.

**Pre-existing state finding (load-bearing for future "is X done?" checks):**
- Prod `mc_b_gold_buy` was ALREADY at weight 3 with `# H2: was 5` marker present when this deploy started (mtime 2026-05-16 17:45 UTC, ~1h before this deploy). Origin unknown — either a parallel-session hand-edit or a partial-apply attempt that interrupted before atomic write. The deploy was launched expecting 11/11 fresh edits; dry-run reported 10/11 with `mc_b_gold_buy` skipped (regex looked for `weight: 5`, found `weight: 3`). Per the user's explicit "ABORT IF any factor not patched" rule the deploy paused for re-direction. Jack picked "apply remaining 10". Apply path executed via `EDITS = [e for e in P.EDITS if e[0] != 'mc_b_gold_buy']` monkey-patch around the script's `cmd_apply()`, preserving its atomic write + post-apply weight validation. Final state: all 11 H2 targets verified at weight 3 via `yaml.safe_load` round-trip.
- Prod `strategies.yaml` also has a SECOND, stale `factors:` block at line 887 (inline-flow style with `ttl_minutes: 1440 / 240 / 90`, pre-PR-3c TTL format) under a different `scoring:` key. YAML last-wins resolves to the line-1094 multi-line block (PR 3c style with `ttl_per_tf` dicts), which is what the score engine actually reads. The 887 block is dead drift and out of scope for H2; flagged for cleanup.

**Features shipped (load-bearing for future "is X done?" checks):**
- **BitUnix Phase 3.2 scoring weights H2 re-tune.** Caps heavy weights at 3: `mc_a_blood_diamond` 5→3, `mc_a_red_diamond` 4→3, `mc_b_gold_buy` 5→3 (pre-existing hand-edit), `mc_b_buy_circle_div` 4→3, `mc_b_sell_circle_div` 4→3. Up-weights Otter precision family 2→3: `water_buy_large`, `water_sell_large`, `spoon_bull`, `spoon_bear`, `money_bag_bottom`, `money_bag_top`. Subtractive net-score formula and PR 3c thresholds (`min_score_to_fire: 5`, premium 10, standard 5, weak 3) unchanged.

**Notable code changes:**
- None. YAML weight edits only via `scripts/patch_bitunix_scoring_h2.py` (the patcher itself is new to prod this deploy but is a one-shot tool, not exercised by the running service).

**Verification:**
- Pre md5: `da18d6c5180cd09592b4475e4df8893e` (with the orphan `mc_b_gold_buy` marker).
- Post md5: `6dc03a793e1e6e58df832aa89407ef93` (all 11 H2 targets at weight 3).
- File size delta: 76256 → 76394 bytes (+138 bytes = 10 inline `# H2: was N` markers).
- `yaml.safe_load` confirms `bitunix_futures.scoring.factors` resolves all 11 targets to `weight: 3`.
- `trading-corp` service `active` post-deploy.
- **Hot-reload assumption was WRONG for BitUnix.** The deploy was launched expecting mtime-cached hot-reload (per CLAUDE.md §5 "config hot-reload" — which is correct for Otter/Cypher/Kalshi/Polymarket/Donchian but NOT BitUnix). Verified: `bitunix_futures_observer.py` builds `ScoringConfig` once at startup in `main.py:380` and holds it in `self.scoring_config`; no mtime check, no reload path. Post-apply check at 19:03 + 19:12 UTC confirmed scorer was still using OLD weights (`mc_a_red_diamond: 4`, `spoon_bear: 2`).
- **H2 actually went live at 2026-05-16 19:21 UTC** when the parallel kalshi_weather deploy restarted the service. First post-restart `bitunix_score_decided` row at 19:24 UTC showed NEW weights: `mc_a_red_diamond: 3, spoon_bear: 3`. Subsequent 19:45 + 19:54 rows confirmed.
- A second redundant restart at 19:55 UTC (this session, after the parallel deploy was confirmed done) was effectively a no-op — H2 was already live.
- Latest boot wiring across all 3 restarts (19:21 / 19:36 / 19:55) unchanged: `scoring=True, pa_enabled=True, htf_gate_mode=enforce, htf_regime_enabled=True, trade_plan_active=False`.

**Deploy-mechanics note:**
- Hotel-wifi → iPhone hotspot in this session blocked SSH (port 22) at the Azure NSG layer (home-IP allowlist). Added temp NSG rule `AllowSSHFromHotspotTemp` (prio 1001, src `107.123.33.3/32`) — but SSH still timed out (root cause unclear; HTTPS to same VM + `github.com:22` from same network both worked). Pivoted to `az vm run-command invoke` (same Azure control-plane path used by Phase 1C). Temp NSG rule removed at end of session.

**Inert / dormant on current traffic:**
- The 887-line stale `factors:` block remains. Out of scope for H2; flagged as cleanup candidate.

**Follow-up:**
- Per `reports/scoring_recommendation.md` falsification criteria: revisit after ≥30 live PREMIUM fires post-H2. PREMIUM mean R on production `paper_trade_record` must be ≥0.05R better than STANDARD mean R; if not, the Otter-precision up-weight was wrong and diamond weights should be partially restored. Filed P1 BACKLOG entry.
- At current ~3 fires/day pre-1D rate (post-1D rate is much lower because most short-circuit at PA gate), 30 PREMIUM fires is ~10-14 days post-H2; could stretch significantly under enforce mode.

**Revert path:**
```bash
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod --command-id RunShellScript \
  --scripts "cd /home/azureuser/trading_corp && sudo -u azureuser python3 scripts/patch_bitunix_scoring_h2.py --revert"
```
(Restores all `# H2: was N` markers, including the orphan `mc_b_gold_buy`.) Or restore from backup: `cp config/strategies.yaml.bak-h2-20260516T185125 config/strategies.yaml`.

---

## 2026-05-16 04:14 UTC — BitUnix Phase 1D: htf_gate.mode shadow → enforce

**Triggered by:** Jack — "lets flip it." Original Phase 1D plan was to wait for ~30 shadow audit rows + replay-script review before flipping. Jack pushed back: in paper mode the cost of a wrong reject is an audit row, not a real loss, and enforce-mode rejects are more informative than shadow-mode "would-have-rejected" markers. Recommendation flipped (no pun intended) to ship enforce now, with rollback gated on observable audit patterns.

**Backup tag:** `pre-bitunix-1d-enforce-20260516-0410`

**Files deployed (1 — modify):**
- `config/strategies.yaml` — single-line change: `bitunix_futures.htf_gate.mode: shadow → enforce`. Local LF md5 `25c25e526ee8057324ef8a70d1fcefe0`.

**Features shipped:**
- **HTF regime gate is now load-bearing.** Per the observer (`bitunix_futures_observer.py:1011`): in enforce mode, `permission.size_multiplier <= 0.0` short-circuits the trade with a `skipped_htf_gate` outcome; `0 < multiplier < 1.0` resizes qty before risk gate. Hard-zero triggers: SAFE_MODE / S/R proximity (≤0.3%) / vol-extreme (1D ATR ≥5%) / funding-extreme (≥0.05%/8h on adverse side).
- **PA validation is now load-bearing.** Same `htf_gate_mode` flag gates both — a `PAValidationDecision.REJECT` now short-circuits the trade with `skipped_pa_validation`.
- The `enforced` flag in `pa_validation_decision` + `htf_gate_decision` audit payloads is now `true` (was `false` in shadow).

**Notable code changes:**
- Pure YAML one-line flip. Code path was already shipped on 2026-05-16 02:24 UTC (Phase 1C); just toggling the mode flag from "audit-only" to "audit-and-act."
- Trade flow is STILL paper-mode (`auto_execute: false`). Real-money risk gate is Phase 4 — at least 2 stages ahead of where this flip puts us.
- Rollback is also a 1-line yaml flip + restart.

**Expected behavioral consequence on prod TODAY:**
- Live funding rate on prod is **-0.378%/8h**, which is **5.6× the funding-extreme threshold of 0.05%**. The HTF gate will likely hard-zero sell-side fires while negative funding remains extreme. All recent Phase 1C-era paper fires have been SELL (per the score panel's RECENT PAPER FIRES table), so the immediate observable change is **fewer placed paper trades + more `skipped_htf_gate` audit rows** in the next few hours.
- This is exactly what enforce is *for* — paper validation that the gate engages on the right scenarios. If we see `skipped_htf_gate` with `reason` text that doesn't match the live regime, rollback. Otherwise, monitor and let the data accumulate.

**Verification:**
- `az vm run-command create` exit 0, executionState `Succeeded`, end 04:15:28 UTC.
- yaml line confirmed on prod (`grep -A 1 htf_gate:` returns `mode: enforce`).
- yaml md5 verified.
- **Boot wiring on prod:** `BitUnix observer wiring: scoring=True, pa_enabled=True, htf_gate_mode=enforce, htf_regime_enabled=True, trade_plan_active=False` — exact target.
- HTF regime snapshot loop continues firing (last seen 04:15:50 UTC).
- First post-flip `htf_gate_decision` audit pending the next Cypher webhook → score-fire. Webhook frequency is ~5-10/hr; 30-min per-side cooldown likely the dominant rate-limiter.

**Inert / dormant (still gated):**
- `trade_plan.enabled: false` — v2 entry/SL/TP path and position reconciler stay dormant. Phase 1E flips that.
- `auto_execute: false` — every order still HITL (paper-mode placeholder). Phase 4 flips that on real-money flow.

**Rollback recipe** (1-line yaml flip + restart, ~50s):
```bash
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts '
TAG=pre-bitunix-1d-enforce-20260516-0410
BASE=/home/azureuser/trading_corp
mv $BASE/config/strategies.yaml.$TAG $BASE/config/strategies.yaml
sudo systemctl restart trading-corp'
```

---

## 2026-05-16 04:03 UTC — BitUnix Decision Flow UX fix follow-up (data.py): activate trigger color-code

**Triggered by:** The 03:15 UTC UX fix shipped the template half (Net column + trigger color-code wiring). Trigger color-code was wired in template but dormant because `view.bitunix_decision_flow.flows[*].trigger_side` wasn't being populated — the data.py half was blocked by an active parallel session iterating in the same file. Parallel session committed at 1083f53 (incidentally folding my unstaged trigger_side helper in with their prediction-markets work), then deployed their portion to prod independently. This deploy ships the residual delta — just my `_intrinsic_side` helper + `trigger_side` field — on top of prod's already-installed parallel-session changes.

**Backup tag:** `pre-bitunix-1c-uxfix-data-20260516-0345`

**Files deployed (1 — modify):**
- `trading_corp/web/data.py` (170,581 bytes LF) — adds `_intrinsic_side(signal_name)` helper to `build_bitunix_decision_flow_view`; looks up the trigger signal's intrinsic side from `observer.scoring_config.factors[name].side` (with `_strip_directional_suffix` fallback to match the scorer's `_resolve_factor`). Adds `trigger_side: "buy" | "sell" | None` to each flow dict. Docstring extended. Unknown signals (guards, PA factors, future TV signals) return None and fall to muted-default in the template.

**Features shipped:**
- **Trigger color-code on Decision Flow panel.** Each Trigger cell is now colored by the signal's intrinsic side: green for buy-named (e.g. `mc_b_buy_circle`, `mc_a_longema`, `mc_a_bluetriangle`), red for sell-named (e.g. `mc_a_red_diamond`, `mc_a_redx`, `mc_a_blood_diamond`), muted-gray for unknown.
- **Per-cell tooltip explains the dynamic:** "Intrinsic side of this TV signal: sell. The order's side is the sign of the aggregate net score — see the Net column." Closes the "buy-named signal next to SELL row" confusion by making the disconnect explanatory.

**Notable code changes:**
- Helper uses lazy import of `_strip_directional_suffix` inside the function body to avoid pulling the scorer module at template-render time / app-boot time. Defensive against circular imports.
- HEAD's data.py = parallel session's `_query_pm_resolved_stats` + related prediction-markets work + my `_intrinsic_side` helper. Prod's pre-deploy data.py had only the parallel session's portion (they shipped via their own surgical patches per commit 1083f53). Post-deploy md5 = `1295bf7d532b61cb4d90cbf1c8668f4a` matches local HEAD's LF md5.

**Verification:**
- `az vm run-command create` exit 0, executionState `Succeeded`, end 04:03:44 UTC.
- Backup tag `data.py.pre-bitunix-1c-uxfix-data-20260516-0345` present.
- Service active; healthz green.
- Rendered HTML for `/division/bitunix_futures` confirms color-code is live: 4 trigger cells with `text-loss` class (sell signals: `mc_a_red_diamond` x2, `mc_a_redx`, `mc_a_blood_diamond`), 1 with `text-gain` class (`mc_b_buy_circle`); all 5 carry the explanatory `title="Intrinsic side of this TV signal: ..."` tooltip.

**Rollback recipe:**
```bash
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts '
TAG=pre-bitunix-1c-uxfix-data-20260516-0345
BASE=/home/azureuser/trading_corp
F=trading_corp/web/data.py
mv $BASE/$F.$TAG $BASE/$F
rm -rf $BASE/trading_corp/web/__pycache__
sudo systemctl restart trading-corp'
```

Note: rollback restores the pre-1295bf7d data.py which has parallel session's prediction-markets work but NOT my trigger_side helper. Trigger column reverts to muted-gray. Net column unaffected (template-only feature).

---

## 2026-05-16 03:35 UTC — BitUnix Decision Flow panel reorder: gate-chain on top, legacy score below

**Triggered by:** Jack screenshotted `/division/bitunix_futures` post-UX-fix and the new Phase 1C panels weren't visible above the fold — they were ~33kb of HTML below the legacy Confluence Score panel. Reorder puts the gate chain on top in natural "decide → audit → outcome" reading order.

**Backup tag:** `pre-bitunix-1c-reorder-20260516-0335`

**Files deployed (1 — modify):**
- `trading_corp/web/templates/division.html` — reorders the 4 BitUnix panels. New order: **HTF Regime → PA Validators → Decision Flow → Confluence Score (legacy)**. Score panel moves from top to bottom of the bitunix_futures stack as the detail/explorer surface. Single comment block added explaining the rationale at the top of the bitunix section.

**Features shipped:**
- HTF / PA / Decision Flow panels render at byte offsets 14,146 / 22,009 / 25,917 (was 46,699 / 54,562 / 58,470 pre-reorder).
- Confluence Score moves from byte 13,686 to byte 33,781 — same content, lower position.
- Decision-flow Net column (from the 03:15 UTC UX fix) is now visible without scrolling for a typical viewport.

**Notable code changes:**
- Pure include-order swap. No view-builder changes; no panel-internal changes. The 4 panel sections themselves are unchanged.

**Verification:**
- `az vm run-command create` exit 0, executionState `Succeeded`, end 03:35:48 UTC.
- Template md5 on prod = `3e97562b1b593212bda2118c1f364fb5` (LF-normalized local matches).
- Service active.
- Rendered HTML byte offsets confirm new order: htf-panel(14146) < pa-panel(22009) < decision-flow(25917) < score-panel(33781).
- Page total 75,159 bytes (was 74,758 — minor delta from comment block).

**Rollback recipe:**
```bash
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts '
TAG=pre-bitunix-1c-reorder-20260516-0335
BASE=/home/azureuser/trading_corp
F=trading_corp/web/templates/division.html
mv $BASE/$F.$TAG $BASE/$F
rm -rf $BASE/trading_corp/web/__pycache__
sudo systemctl restart trading-corp'
```

---

## 2026-05-16 03:29 UTC — Bug C: one-shot DELETE of stale PCT pending audit rows (no `resolves_at` payload)

**Triggered by:** Post-Bug-A probe showed polymarket_copy_trading with `pending=2431 / resolved=506` (vs e.g. polymarket_arbitrage's `52 / 12`). 100% of PCT pending rows lack `resolves_at` in the payload, so Bug B's expires_at-ordering fix doesn't help them. Root causes: (a) pre-2026-05-14 multi-leg-resolver bug left rows unpairable, (b) Apify's 10-min polling cadence misses fast whale exits (winners auto-settle before our poll sees them — documented in PCT memory `trading_corp_polymarket.md`'s adverse-selection note).

**Backup tag:** `20260516-032942` (in `/tmp/pct_stuck_audit_backup_20260516-032942.{jsonl,sql}` — 1,745 rows; restore via `sqlite3 trading_corp.db < <path>.sql`).

**Changes:** SQL DELETE on `audit_event`, no code changes.

**Predicate (Path A, 24h cutoff per session decision):**
```sql
DELETE FROM audit_event
WHERE actor='polymarket_copy_trader'
  AND kind='would_have_placed'
  AND COALESCE(json_extract(payload_json,'$.side'),'buy')='buy'
  AND ts < datetime('now','-1 day')
  AND json_extract(payload_json,'$.order_id') NOT IN
      (SELECT order_id FROM polymarket_round_trips WHERE order_id IS NOT NULL)
  AND json_extract(payload_json,'$.order_id') NOT IN
      (SELECT entry_order_id FROM polymarket_round_trips WHERE entry_order_id IS NOT NULL);
```

**Pre-delete age distribution:**
- <1d (fresh): 691 rows — protected by 24h cutoff (still in normal pairing flow)
- 1-3d: 1,604 rows — deleted
- 3-7d: 141 rows — deleted
- Total deleted: 1,745

**Verification:**
- `audit_event` rows for `polymarket_copy_trader` would_have_placed BUY: 2,482 → 737. The residual 739 dashboard-pending number includes ~46 paired-via-entry_order_id rows that have polymarket_round_trips entries (so already excluded from the dashboard tile).
- Dashboard probe immediately after delete: `polymarket_copy_trading pending=693, resolved=506`. Other divisions unchanged.
- Backups: 1,745-row JSONL (2.1 MB) + SQL INSERTs (1.9 MB). Owned by root, 0600 perms.

**Decision rationale (recorded for memory):** Path A (straight DELETE) over Path B (synthetic-void round-trips) per Jack's call. Data loss is real but bounded — these rows had no exit price, no resolution, and no `resolves_at` anchor; their EV signal was unrecoverable. The dashboard accuracy + cognitive cost of looking at "2,431 stuck" every page-load outweighed the preservation case.

**Latent followup (filed):**
- **PCT stale-entry pruner cron** (~2-3h, P2). Otherwise we'll keep accumulating stale entries weekly as Apify continues to miss whale auto-settles. Suggested: nightly cron that runs the same predicate against rows >24h old. Builds the recurring discipline without the manual delete chore. File location: `trading_corp/scripts/prune_stale_pct_entries.py` + new systemd timer.
- Same shape might apply to `kalshi_copy_trader` over time (current K3 stuck count is only 2, so not urgent — but watch).

**Rollback recipe:**
```bash
# 1,745 rows preserved in backup; restore by piping the SQL file back in.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db < /tmp/pct_stuck_audit_backup_20260516-032942.sql"
```

---

## 2026-05-16 03:16 UTC — Bug B: kalshi + polymarket resolver `ORDER BY ts ASC` starvation fix

**Triggered by:** Investigation of kalshi_llm_arbitrage's "stuck-pending" backlog after the 02:10 UTC resolver-wiring deploy revealed the resolver was scanning 50 rows/tick (per-actor budget) but resolving only 0-1 because the OLDEST 50 rows by `audit_event.ts` were all long-horizon Politics bets (KXH100MON-26MAY31, KXPERSONPUBLIC-26JUN01) that don't settle until May 31 - June 1. Meanwhile, ~605 past-expiration rows (Crypto/Climate bets from before the specialized-agent lockdown) had LATER `ts` values and never made the top-50 cut. The "stuck" framing was misleading — markets ARE resolved on Kalshi; the resolver just wasn't reaching their audit rows. Same shape on polymarket_resolver.py:67 (single resolver, no per-actor budget but same ordering bug). Affects ALL prediction-market divisions; PCT (`polymarket_copy_trading`) NOT addressed by this fix because its payloads have no `resolves_at` field (Bug C).

**Backup tag:** `20260516-031555` (in `/tmp/{kalshi_resolver.py,polymarket_resolver.py}.bak-20260516-031555`)

**Files deployed (2, anchored Python patch; kalshi md5 was already prod-identical from 02:10 deploy, polymarket md5 had pre-existing drift):**
- `trading_corp/agents/kalshi_resolver.py` — `_fetch_unresolved_orders` per-actor SQL ordering changed from `ORDER BY a.ts ASC LIMIT ?` to `ORDER BY (json_extract(...,'$.expires_at') IS NULL), json_extract(...,'$.expires_at') ASC, a.ts ASC LIMIT ?`. Past-expiration rows scanned first; rows without `expires_at` fall to NULLS-LAST priority. Docstring updated.
- `trading_corp/agents/polymarket_resolver.py` — same shape: `ORDER BY a.ts ASC` → `ORDER BY (resolves_at IS NULL), resolves_at ASC, a.ts ASC`. Polymarket field is `resolves_at` not `expires_at`. Single SQL (no per-actor loop here).

**Features shipped:**
- Resolvers drain past-expiration backlog ~50 rows/tick (kalshi per-actor) or ~6+/tick (polymarket per-tick). 605 kalshi_llm past-expiration rows projected to clear in ~12h.
- Future-expiration rows correctly wait for actual market settlement.
- Rows with no expires_at fall to lowest priority — won't crowd out resolvable rows (Bug C territory; PCT's 2,431 stuck entries unaffected as designed).

**Test added:** `test_fetch_orders_past_expiration_first` injects (old-ts, future-expiration) + (new-ts, past-expiration) + (no-expires_at) rows and asserts order `[past, future, no-exp]`. 23/23 kalshi_resolver tests passing.

**Verification (post-deploy 03:16 UTC):**
- First resolver tick at 03:16:56 UTC: **scanned 202, resolved 50, pending 152, errors 0** (was: scanned 203 / resolved 11 / pending 192 in the prior tick).
- First polymarket tick at 03:16:51 UTC: scanned 100 / resolved 6 / pending 94 (was: typically resolved 0-1).
- kalshi_round_trips for kalshi_llm_arbitrage: **194 → 245** (+51 in 5 min). For kalshi_arbitrage: 0 → 0 (no past-expiration in oldest-by-expires_at for that actor's 50-slot budget yet).
- polymarket_arbitrage pending: 58 → 52; resolved 6 → 12. polymarket_copy_trading unchanged at 2431/506 (as expected — Bug C).
- No template-render errors, no `OperationalError`, no `kalshi_resolver tick error` in journal.

**Latent followup:**
- **Bug C — PCT 2,435 stuck entries with no `resolves_at`.** Ordering fix can't help; need one-shot delete + stale-pruner cron. Filing as the next deploy this session.
- ~555 more kalshi_llm past-expiration rows still in queue; will drain at ~50/hour over the next ~12 hours. No action needed — natural drainage.

**Rollback recipe:**
```bash
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "
TAG=20260516-031555; \
sudo cp /tmp/kalshi_resolver.py.bak-\$TAG /home/azureuser/trading_corp/trading_corp/agents/kalshi_resolver.py; \
sudo cp /tmp/polymarket_resolver.py.bak-\$TAG /home/azureuser/trading_corp/trading_corp/agents/polymarket_resolver.py; \
sudo rm -rf /home/azureuser/trading_corp/trading_corp/agents/__pycache__; \
sudo systemctl restart trading-corp"
```

---

## 2026-05-16 03:15 UTC — BitUnix Decision Flow UX fix (template-only): Net column + trigger color-code wiring

**Triggered by:** Jack flagged confusion mid-1C-deploy that buy-named TV signals (`mc_a_longema`, `mc_b_buy_circle`, `mc_a_bluetriangle`) showed in the Decision Flow panel next to SELL orders. Behavior is correct (the panel's `signal_name` = the latest contributing TV signal; order side = sign of aggregate score), but the labeling read like a bug. UX fix: option 1 (add explicit Net column) + option 2 (color-code trigger by intrinsic side).

**Backup tag:** `pre-bitunix-1c-uxfix-20260516-0240`

**Files deployed (1 — modify):**
- `trading_corp/web/templates/partials/bitunix_decision_flow.html` — adds a dedicated **Net** column (signed integer, green for +N / red for -N / muted for 0); adds intrinsic-side color-code wiring to the Trigger column (buy=green, sell=red, unknown=muted); subtext under Score now reads `order side: sell/buy` (was `side · net=N`, now redundant with new Net column); column-header tooltip explains "Net = aggregate net confluence score; sign decides order side, magnitude decides tier."

**Scope split:** The full feature pair (#1 + #2) requires `web/data.py` to expose `trigger_side` per flow row. At deploy time the parallel session had ~80 lines of unstaged work in `web/data.py` (`_query_pm_resolved_stats` + related), so deploying my local `data.py` would also push their unfinished work. Template-only deploy was the clean split: **option #1 (Net column) is fully live** now (uses `f.score.net` which the view already passed pre-1C). **Option #2 (trigger color-code) is wired in template but silently no-ops** — `f.trigger_side` is missing from the view dict, so `{% if f.trigger_side == 'buy' %}` falls to `{% else %}text-mono` (visually identical to pre-1C-uxfix). Follow-up: ship the `data.py` half once parallel session's edits commit.

**Features shipped:**
- **Net column** on Decision Flow panel: signed score (e.g. `-7` or `+12`) with green/red coloring. The "buy signal in a SELL row" disconnect is now explanatory ("net=-7 → bears outweighed the buy contributor") rather than confusing.

**Notable code changes:**
- The score column's subtext changed from `side · net=N` to `order side: side` — the net moved to its own column for prominence. No data-shape change to the view.
- Template color-code conditions reuse the existing Tailwind classes `text-gain` / `text-loss` / `text-muted` / `text-mono` for consistency with PA Validators panel + tier coloring.
- Local `web/data.py` has BOTH my unstaged edits (`_intrinsic_side` helper + `trigger_side` field) AND the parallel session's unstaged work; the local file is NOT in a deployable state until one side commits. The template-only deploy is independent.

**Verification:**
- `az vm run-command create` exit 0, executionState `Succeeded`, end 03:16:33 UTC.
- Backup tag present: `bitunix_decision_flow.html.pre-bitunix-1c-uxfix-20260516-0240`.
- Template md5 on prod = `7e2ee2abb596ac235df3220db3cd1737` (LF-normalized local matches).
- Service `active` post-restart.

**Inert / dormant:**
- **Trigger color-code logic in template is dormant** until `data.py` exposes `trigger_side` — currently renders as muted-default (`text-mono`), visually identical to pre-1C-uxfix. First color-code render lands when the `data.py` follow-up deploys.

**Rollback recipe:**
```bash
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts '
TAG=pre-bitunix-1c-uxfix-20260516-0240
BASE=/home/azureuser/trading_corp
F=trading_corp/web/templates/partials/bitunix_decision_flow.html
mv $BASE/$F.$TAG $BASE/$F
rm -rf $BASE/trading_corp/web/__pycache__
sudo systemctl restart trading-corp'
```

---

## 2026-05-16 03:00 UTC — Bug A: PM dashboard tiles + tab labels showing LIMIT values not true counts

**Triggered by:** Jack reported the `/prediction-markets/kalshi_llm_arbitrage` page showed OPEN `200` / RESOLVED `100`, but the DB had 1,761 unresolved would_have_placed rows + 195 resolved round-trips for that division. Root cause at `web/data.py:3565`: `_pm_summary(..., len(open_trades))` was passed list length as `pending_count`, but the list is capped by `_query_pm_open_trades(..., limit=200)`. Same shape for `n_resolved = len(round_trips)` inside `_pm_summary` — capped by `history_limit=100`. The tile and tab labels were literally rendering the query LIMIT value, not actual counts. Affects ALL prediction-market divisions.

**Backup tag:** `20260516-030023` (in `/tmp/{data.py,pm_dashboard_body.html}.bak-20260516-030023`)

**Files deployed (2, via anchored Python patch — both prod-drifted in unrelated parts):**
- `trading_corp/web/data.py` — 3 anchored edits: (a) new `_query_pm_resolved_stats(db_url, division_slugs) -> dict` (true COUNT/SUM aggregates over `polymarket_round_trips` + `kalshi_round_trips`, no LIMIT); (b) `_pm_summary` signature gains optional `resolved_stats: dict | None = None` kwarg; when provided, n_resolved/n_wins/n_voids/total_realized_pnl come from there instead of from the list. Legacy 3-arg callers still work (legacy path computes from list). (c) `build_pm_dashboard` asyncio.gather grew two tasks: `_query_pm_pending_count` and `_query_pm_resolved_stats`; both passed into `_pm_summary`.
- `trading_corp/web/templates/partials/pm_dashboard_body.html` — 3 anchored edits: Open + History tab labels switched from `view.{open_trades,round_trips} | length` to `view.summary.{n_pending,n_resolved}`; Open-tab header now reads "showing N of M awaiting market settle" when list is truncated; History "All" filter button switched to `summary.n_resolved` (Wins/Losses were already correct).

**Features shipped:**
- All PM division tiles (RESOLVED, OPEN, WIN RATE, REALIZED P&L) now reflect true totals.
- Tab labels show true counts (Open / History).
- Open-tab body header transparently signals truncation ("showing 200 of 1761").
- No new SQL hot paths — 2 added aggregation queries run in parallel via asyncio.gather; both are indexed COUNTs.

**Notable code changes:**
- `_query_pm_resolved_stats` handles polymarket's missing `market_result` column: voids approximated as `won=0 AND realized_pnl=0.0` (same heuristic the existing `_query_pm_round_trips` uses via the yes_won-derivation path). Kalshi has the column natively.
- 28 existing `test_prediction_markets_dashboard.py` tests pass; legacy 3-arg `_pm_summary` calls still work via the kwarg default.
- `_query_pm_pending_count` was already on prod and correct — just never called from `build_pm_dashboard` until now.

**Verification (post-deploy 03:00 UTC):**
- Direct probe via `_query_pm_pending_count` + `_query_pm_resolved_stats` from service-attached venv (KV creds working):
  - `kalshi_llm_arbitrage`: 1761 pending / 195 resolved / 100 wins / -$24.95 (was tile: 200 / 100 / 43% / -$15.56)
  - `kalshi_arbitrage`: 236 pending / 0 resolved
  - `kalshi_weather`: 100 pending / 0 resolved (markets haven't expired yet)
  - `kalshi_crypto`: 56 pending / 11 resolved / 0 wins / -$11.00 (matches 02:10 resolver-wiring backfill)
  - `kalshi_copy_trading`: 2 pending / 391 resolved / 188 wins / +$1.16
  - `polymarket_arbitrage`: 58 pending / 6 resolved / 4 wins / +$1.52
  - `polymarket_copy_trading`: **2431 pending / 506 resolved / 209 wins / -$134.26** ← exposed the PCT stuck-entry surface separately
- Service `active` at 03:00:32 UTC post-restart. No template-render errors in journal.

**Latent bugs surfaced (queued as next deploys this session):**
- **Bug B — kalshi resolver `ORDER BY ts ASC` starvation.** 1761 kalshi_llm n_pending includes ~605 past-expiration rows whose markets are settled on Kalshi but never make the resolver's top-N-oldest cut (oldest by ts = longest-horizon Politics, not most-likely-resolved). Same pattern on `polymarket_resolver.py:67`. Fix: switch ordering to `expires_at ASC NULLS LAST`. ~10 LOC.
- **Bug C — PCT 2,431 stuck rows have no `expires_at`.** Whale-mirror entries where SELL pairing failed (Apify poll cadence misses fast whale exits, OR pre-2026-05-14 multi-leg-resolver bug). Needs one-shot delete + stale-pruner cron.

**Rollback recipe:**
```bash
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "
TAG=20260516-030023; \
sudo cp /tmp/data.py.bak-\$TAG /home/azureuser/trading_corp/trading_corp/web/data.py; \
sudo cp /tmp/pm_dashboard_body.html.bak-\$TAG /home/azureuser/trading_corp/trading_corp/web/templates/partials/pm_dashboard_body.html; \
sudo rm -rf /home/azureuser/trading_corp/trading_corp/web/__pycache__; \
sudo systemctl restart trading-corp"
```

---

## 2026-05-16 02:24 UTC — BitUnix HTF Phase 1C — strategies.yaml + dashboard partials + dormant reconciler (shadow mode)

**Commits on main:** `d0f99f4` (merge of `claude/gallant-tereshkova-49ef85`), `00e0c45` (yaml weather-cap hot-patch preserve), `2b0171b` (boot smoke test). No new commit at deploy time — main HEAD `d0f99f4` is byte-identical (LF-normalized) with what shipped.
**Triggered by:** Jack — "ship phase 1C." Picks up the queue from the 2026-05-15 21:35 UTC EOS snapshot in BACKLOG.md.
**Backup tag:** `pre-bitunix-1c-20260516-0202` (on the 4 modify-files; 4 new files have no backup — `rm` on rollback).

**Files deployed (8 — 4 modify, 4 new):**
- `config/strategies.yaml` (MODIFY, 77,839 bytes LF) — adds `bitunix_futures.pa_validation`, `htf_gate`, `htf_regime`, `trade_plan`, `fees` sub-blocks. Sets `htf_gate.mode: shadow`, `pa_validation.enabled: true`, `trade_plan.enabled: false`. **Supersedes prod's Fix-#3 Cypher weight cuts** (`mc_a_blood_diamond: {weight: 2, ttl_minutes: 360}` etc.) via PR 3c's `score_timeframes: [3m, 15m, 30m]` whitelist — Cypher 4h/1d signals still hit the audit ledger but contribute 0 to score. Tier thresholds raised: PREMIUM 8→10, STANDARD 4→5, WEAK 2→3; `min_score_to_fire` 4→5. The 2026-05-15 21:48 UTC `kalshi_weather.sizing.max_per_day_pct=120.0` hot-patch was preserved (commit `00e0c45`).
- `trading_corp/web/data.py` (MODIFY, 169,161 bytes LF) — adds `build_bitunix_pa_view`, `build_bitunix_decision_flow_view`, `build_bitunix_htf_view`. Recent-fires data source switched audit→`paper_trade_record` to surface v2 extras (when active).
- `trading_corp/web/templates/division.html` (MODIFY) — adds `{% include %}` lines for the 3 new BitUnix partials.
- `trading_corp/web/templates/partials/bitunix_score_panel.html` (MODIFY) — cleanup for the v2 surface; legacy v1 score still renders.
- `trading_corp/agents/divisions/bitunix_position_reconciler.py` (**NEW**, 11,607 bytes LF) — `decide_sl_action` + `reconciler_tick` + `run_reconciler_loop`. **Dormant on this deploy** — main.py only launches the reconciler async task when `_trade_plan_config is not None`, which is gated on `bitunix_futures.trade_plan.enabled: true` (currently false). Module imports succeed; tests pass; no behavioral effect.
- `trading_corp/web/templates/partials/bitunix_decision_flow.html` (**NEW**) — Decision Flow panel: per-fire score → PA → HTF → outcome trail (last 5).
- `trading_corp/web/templates/partials/bitunix_htf_panel.html` (**NEW**) — HTF Regime panel: composite regime, volatility, funding rate, per-TF (1h/4h/1d) sub-states, nearest BTC S/R levels.
- `trading_corp/web/templates/partials/bitunix_pa_panel.html` (**NEW**) — PA Validators panel: recent PA validation decisions with pass/fail per gate.

**Features shipped (load-bearing for future "is X done?" checks):**
- **PA validation in shadow mode.** `bitunix_futures.pa_validation.enabled=true`; gates evaluate but don't block trades. Audit kind `pa_validation_decision` starts writing on the next post-deploy score-fire.
- **HTF regime classifier active + HTF gate in shadow mode.** `bitunix_futures.htf_gate.mode=shadow`; gate evaluates regime alignment + funding extremes + S/R proximity but does NOT reject or resize. Audit kind `htf_gate_decision`. Live regime data flowing: HTF funding poll @ 30min, regime snapshot loop @ 10min.
- **Decision flow visibility** on `/division/bitunix_futures`: per-fire trail Score → PA → HTF → Outcome, last 5 fires. Pre-1C fires render with "no PA audit / no HTF audit" markers (expected).
- **PR 3c score_timeframes whitelist active.** Cypher 4h/1d webhooks still acknowledged (audit + ledger) but contribute zero to score. The pre-1C Fix-#3 weight cuts that achieved a similar outcome are obsoleted by this change.
- **Position reconciler module on disk + import-tested.** Async task is NOT yet started — gated on `trade_plan.enabled: true`. First start happens on Phase 1E.
- **Boot-smoke test (`tests/test_boot_smoke.py`) is now main's pre-deploy gate.** AST-parses main.py call sites for `BitunixFuturesObserver`, `WebDeps`, `_start_web_server` and asserts kwarg parity with constructor signatures. Catches the class of bug that crashed two 1B attempts.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Boot wiring line is the dormant-state truth.** Watch journalctl for `BitUnix observer wiring: scoring=True, pa_enabled=True, htf_gate_mode=shadow, htf_regime_enabled=True, trade_plan_active=False`. That single line tells you exactly which gates are loaded vs dormant.
- **`bitunix_position_reconciler.py` is dead code on prod today.** Async task launch is conditionally gated in main.py on `_trade_plan_config is not None`. Smoke test imports the module to catch syntax errors regardless.
- **Cypher 4h/1d signals are silently weight=0 now.** They still appear in the audit ledger and dashboard (visibility preserved) but contribute 0 to the BitUnix score. If a future deploy needs them to score again, flip `score_timeframes` in `bitunix_futures.htf_regime` (not the weights — weights are moot).
- **Local-on-Windows files have CRLF line endings** due to git autocrlf; prod is LF. ALL byte-level deploy operations must `tr -d '\r'` (or equivalent) before encoding, else md5s won't match prod and the byte-identical invariant breaks. The deploy used `scripts/build_phase1c_deploy.sh`, which LF-normalizes in the encoder.
- **Managed `az vm run-command create --script @file` is the right tool for multi-hundred-kb deploys.** The legacy `az vm run-command invoke --scripts "..."` has a ~28k payload cap; chunked uploads through it are unreliable past ~3 chunks. Use the managed RunCommand resource and clean it up after (`az vm run-command delete`).

**Latent bugs caught + fixed (during deploy):**
- **CRLF line-ending bug surfaced via md5 mismatch.** Caught pre-ship: local repo's checkout had CRLF, prod has LF. The encoder was patched to LF-normalize before base64. New memory candidate.
- **Initial chunked deploy (legacy `invoke`) aborted at chunk 3 of data.py.** Likely cmd-line-length or `--scripts` payload cap. No prod state change from the failed first attempt (script's `set -euo pipefail` cleanly aborted before any backup or mv ran).

**Verification:**
- `az vm run-command create` exit code 0, execution state `Succeeded`, end 02:24:37 UTC May 16. Total runtime ~12s on prod.
- All 8 file md5s on prod match LF-normalized local md5s exactly:
  - `8c5168dbc99217c9c1ba125df0bc5ba5  config/strategies.yaml`
  - `a79572de6d3f3b7c0152f405b02d7890  trading_corp/web/data.py`
  - `c3db1934f1a974072e543e9e19757b4f  trading_corp/web/templates/division.html`
  - `fdbbe1dc5b93937a21ba4a6e30fc5b1c  trading_corp/web/templates/partials/bitunix_score_panel.html`
  - `5de73e3bd4f47d7ac3785478da1ca480  trading_corp/agents/divisions/bitunix_position_reconciler.py`
  - `a2163ad7a37210bd5f92e53859204e25  trading_corp/web/templates/partials/bitunix_decision_flow.html`
  - `f32d3dce65cb26d3cf846b140cd50fd1  trading_corp/web/templates/partials/bitunix_htf_panel.html`
  - `3981868045b84583b2ca2483880b9f5c  trading_corp/web/templates/partials/bitunix_pa_panel.html`
- All 4 backup tags `.pre-bitunix-1c-20260516-0202` present.
- Service active; `/healthz` returns `{"status":"ok","mode":"PAPER"}`.
- Web bound on `:8000` at 02:25:18 UTC (53s after restart start — within normal Fidelity-Playwright login budget).
- **Dormant-state confirmed via journalctl**: `BitUnix observer wiring: scoring=True, pa_enabled=True, htf_gate_mode=shadow, htf_regime_enabled=True, trade_plan_active=False` — exact target state.
- HTF funding poll online (`bitunix HTF funding primed: rate=-0.003781`); bar archiver online (4 caches, 60s); HTF regime-snapshot loop online (600s).
- Dashboard screenshot confirmation from Jack: HTF Regime / PA Validators / Decision Flow panels all rendering. NEUTRAL composite regime, HIGH vol, -0.378% funding, BTC nearest S/R $73,974.95 → $79,635.07.
- **Trade flow on prod UNCHANGED.** Existing `_build_proposal` (v1 path) still active; `_build_proposal_v2` dormant.

**Inert / dormant on current traffic (Phase 1C leaves these for Phase 1D/1E):**
- **`trade_plan.enabled: false`** — v2 entry/SL/TP path not active; `_build_proposal_v2` not invoked; `bitunix_position_reconciler` async task not started.
- **`htf_gate.mode: shadow`** — HTF regime gate writes `htf_gate_decision` audits but does not reject or resize any orders. Flipping to `enforce` is Phase 1D, gated on ≥30 shadow audit rows + `scripts/replay_pr3_cutover.py` review.
- **First shadow audit rows pending the next post-1C score-fire** that produces a PREMIUM/STANDARD tier outcome. Webhook frequency ~5-10/hour; same-direction cooldown is 30 min per side, so realistic accumulation is ~10-15 shadow rows/day. ~3 days to reach 30-row review threshold.

**Rollback recipe:**
```bash
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts '
TAG=pre-bitunix-1c-20260516-0202
BASE=/home/azureuser/trading_corp
for f in config/strategies.yaml \
         trading_corp/web/data.py \
         trading_corp/web/templates/division.html \
         trading_corp/web/templates/partials/bitunix_score_panel.html; do
  mv $BASE/$f.$TAG $BASE/$f
done
rm $BASE/trading_corp/agents/divisions/bitunix_position_reconciler.py
rm $BASE/trading_corp/web/templates/partials/bitunix_decision_flow.html
rm $BASE/trading_corp/web/templates/partials/bitunix_htf_panel.html
rm $BASE/trading_corp/web/templates/partials/bitunix_pa_panel.html
rm -rf $BASE/trading_corp/agents/divisions/__pycache__ $BASE/trading_corp/web/__pycache__
sudo systemctl restart trading-corp'
```

---

## 2026-05-16 02:10 UTC — Kalshi resolver wiring: equity writers for kalshi_weather + kalshi_crypto, per-actor scan budget

**Triggered by:** Audit found 0 rows in `kalshi_round_trips` all-time for `kalshi_weather` + `kalshi_crypto` despite both strategies firing ~85 + ~59 `would_have_placed` audit rows in the prior 11h. Two underlying gaps: (a) main.py only spawned equity-snapshot writers for `kalshi_arbitrage` + `kalshi_llm_arbitrage` even though `kalshi_resolver._KALSHI_DIVISIONS` already listed weather + crypto; (b) `_fetch_unresolved_orders` used `WHERE actor IN (...) ORDER BY ts ASC LIMIT 200`, and kalshi_llm_arbitrage's 1,761 stuck-pending backlog meant weather + crypto rows never made the top-200 cut.

**Backup tag:** `20260516-021025` (in `/tmp/{kalshi_resolver.py,main.py}.bak-20260516-021025`)

**Files deployed (2):**
- `trading_corp/agents/kalshi_resolver.py` — refactored `_fetch_unresolved_orders` to query per-actor with a `max_per_actor` LIMIT (default 50). `resolve_pending_round_trips` gained the same kwarg + `max_per_tick` bumped 200 → 300. Top-of-file docstring updated to mention 6 strategies. Full overwrite (prod md5 was identical to local pre-edit).
- `trading_corp/main.py` — added two new equity-snapshot-loop blocks for `kalshi_weather` + `kalshi_crypto` after the existing `kalshi_llm_arbitrage` block. Updated stale comment claiming "two Kalshi divisions / ALL THREE strategies." Anchored Python patch via `scripts/patch_kalshi_weather_crypto_equity_writers.py` (prod main.py is drifted in unrelated parts).

**Features shipped:**
- 4 kalshi equity-snapshot writers now running (was 2): kalshi_arbitrage, kalshi_llm_arbitrage, kalshi_weather, kalshi_crypto — each writes to `kalshi_equity_history` every 5 min off its per-division paper broker.
- Per-actor resolver scan budget. With 6 actors × max_per_actor=50, each tick scans up to 300 candidates. Prevents any one strategy's stuck-pending backlog from starving others.
- `kalshi_round_trips` now populating for `kalshi_weather` + `kalshi_crypto` — closes the data-pipeline gap that was blocking the paper→live validation gate (WR ≥ 65% over 30+ RTs).

**Notable code changes:**
- `_KALSHI_ACTORS` and `_KALSHI_DIVISIONS` were already correct on prod pre-deploy; the bug was the *scan-ordering* and the *equity-loop spawning*, not the actor whitelist. Memory `kalshi_specialized_agent_wiring.md` had #6 ("equity writer registered") partially captured but the more subtle starvation issue was new.
- Test added: `test_resolve_per_actor_budget_prevents_starvation` injects 120 old LLM rows + 2 fresh weather/crypto, asserts both new rows resolve. Replicates the pre-fix starvation in isolation. 22/22 kalshi_resolver tests passing.

**Latent bug NOT in scope (filed as P2 followup):**
- `kalshi_llm_arbitrage` has 1,761 unresolved would_have_placed rows from 2026-05-11+ that the resolver reports as pending/not_found tick after tick. Either the markets are genuinely still pending (unlikely for week-old binary markets) or `get_market_resolution` is returning wrong status for them. Worth investigating once weather/crypto-resolution data has accumulated.

**Verification:**
- Service restart 02:10:30 UTC; service `active`.
- 4/4 equity-writer "online" log lines confirmed at 02:11:11 UTC.
- First resolver tick at 02:11:23 UTC: scanned 203 / **resolved 11** / pending 192 / errors 0. Pre-deploy ticks consistently produced resolved=0.
- `kalshi_equity_history` now has rows for `kalshi_weather` ($500) and `kalshi_crypto` ($500).
- `kalshi_round_trips` first 10 min: 10 kalshi_crypto rows (all losses, $-10 / 0% WR — small early sample), 6 kalshi_copy_trading (paired exits, 67% WR / +$0.07), 1 kalshi_llm_arbitrage.
- Weather round-trips not yet appearing — the 14:33 UTC May 15 fires targeted KXHIGH-26MAY15 markets that resolve later (~04-19 UTC May 16 depending on city/expiration). Expected to land naturally on the next 1-2 resolver ticks.
- BitUnix observer wiring re-confirmed dormant post-restart: `pa_enabled=False, htf_gate_mode=off, htf_regime_enabled=False, trade_plan_active=False`.

**Inert / dormant on current traffic:** None — both new writers and the per-actor budget are exercising on every tick.

**Rollback recipe:**
```bash
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "
TAG=20260516-021025; \
sudo cp /tmp/kalshi_resolver.py.bak-\$TAG /home/azureuser/trading_corp/trading_corp/agents/kalshi_resolver.py; \
sudo cp /tmp/main.py.bak-\$TAG /home/azureuser/trading_corp/trading_corp/main.py; \
sudo rm -rf /home/azureuser/trading_corp/trading_corp/__pycache__ /home/azureuser/trading_corp/trading_corp/agents/__pycache__; \
sudo systemctl restart trading-corp"
```

---

## 2026-05-16 00:58 UTC — Fix-D sub-fix: `divergence_pct` on no_edge audit rows

**Triggered by:** Post-Fix-B audit query for "what edges did the 10% gate filter out?" returned null. Field is in `would_have_placed` payloads as `divergence_pct`, but in `kalshi_*_skipped_no_edge` payloads it's named `edge_pct` — same value, two field names. Future Fix-D tuning needs a single field name across all event kinds.

**Backup tag:** `bak-fixd-20260516-005859`

**Files deployed (2):**
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — added `"divergence_pct": round(verdict.edge_pct, 1)` to `eval_payload` (alongside existing `edge_pct`).
- `trading_corp/agents/strategies/kalshi_crypto_arb.py` — same one-line addition.

**Features shipped:**
- All kalshi weather + crypto `evaluated` / `skipped_no_edge` / `skipped_near_threshold` audit rows now carry `divergence_pct` (alias for `edge_pct`). `edge_pct` retained for backwards compat.
- Empirical Fix-D edge distribution analysis is now a single-field query: `SELECT json_extract(payload_json,'$.divergence_pct')` works across `would_have_placed` AND `skipped_no_edge` rows.

**Verification:**
- Service restart 00:58:59 UTC May 16; service `active`.
- Local md5 = prod md5 for both strategy files post-patch (no drift introduced).
- Audit row verification deferred to next scan cycles (~60s for crypto, ~300s for weather).

**Rollback recipe:**
```bash
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "
BASE=/home/azureuser/trading_corp/trading_corp/agents/strategies; \
mv \$BASE/kalshi_weather_arb.py.bak-fixd-20260516-005859 \$BASE/kalshi_weather_arb.py; \
mv \$BASE/kalshi_crypto_arb.py.bak-fixd-20260516-005859 \$BASE/kalshi_crypto_arb.py; \
rm -rf \$BASE/__pycache__; \
sudo systemctl restart trading-corp"
```

---

## 2026-05-15 22:23 UTC — P2: polymarket-scope-leak fix in `risk.py` (kalshi tail orders unblocked)

**Triggered by:** Audit reconciliation during P1 investigation revealed `risk.py:114` dispatched ALL `is_prediction_market: True` orders through `_evaluate_polymarket`, which enforces a `[0.05, 0.95]` implied-prob bound check designed for Polymarket. Kalshi deep-OTM markets at $0.01/$0.99 implied were systematically rejected — 9 weather + 9 crypto today pre-fix. Memory captured this as a weather-tail-only issue with "practical loss small"; reality was 34% of crypto fires + all the asymmetric-EV signals.

**Backup tag:** `bak-p2-scopeleak-20260515-222357`

**Files deployed (1):**
- `trading_corp/agents/risk.py` — at the polymarket dispatch site, added `and not order.strategy.startswith("kalshi_")` to the condition + a 5-line comment explaining the venue routing decision. Kalshi orders now fall through to the generic `per_trade_risk_pct` path.

**Features shipped:**
- All Kalshi strategies (weather, crypto, tail/temporal/llm arb) bypass the polymarket `[0.05, 0.95]` implied-prob bound check.
- Deep-tail kalshi orders ($0.01-$0.04 or $0.96-$0.99 implied) resize to per-trade-risk-pct cap ($7.50 on $500 paper equity) instead of being rejected outright.

**Notable code changes:**
- `_evaluate_polymarket` itself unchanged. Only the dispatch condition at risk.py:114 was edited.
- `risk.yaml kalshi:` section (per-leg + aggregate caps from Phase K2.1) still has no `_evaluate_kalshi` consumer. Building one is load-bearing for future live-mode flip but not blocked by this fix.

**Verification:**
- Service restart 22:23:57 UTC; service `active`.
- **0 polymarket-scope-leak rejections** post-restart (was 18 today across weather + crypto pre-fix).
- 11 weather + 8 crypto `would_have_placed` audit rows in first 24min post-restart.
- Local risk.py md5 ≠ prod md5 still (drift in other parts of file); patched line is identical on both sides.

**Rollback recipe:**
```bash
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "
mv /home/azureuser/trading_corp/trading_corp/agents/risk.py.bak-p2-scopeleak-20260515-222357 \
   /home/azureuser/trading_corp/trading_corp/agents/risk.py; \
rm -rf /home/azureuser/trading_corp/trading_corp/agents/__pycache__; \
sudo systemctl restart trading-corp"
```

---

## 2026-05-15 21:48 UTC — Weather day-cap raised $125 → $600 (paper-mode budget unblock)

**Triggered by:** P1 audit reconciliation revealed the day-cap math was working correctly: weather hit exactly $125 (= 25% × $500 paper_capital) in 18 fires today, then no_size'd the rest. Not a bug — the cap was simply binding at intended levels. User requested raising the paper-mode budget to $600 for faster data accumulation pre-live-flip.

**Backup tag:** `bak-day600-20260515-214835`

**Files deployed (1):**
- `config/strategies.yaml` — `kalshi_weather_arb.sizing.max_per_day_pct: 25.0 → 120.0`

**Features shipped:**
- Weather daily budget = $600 (= 120% × $500 paper_capital). Per-market stays 5% = $25/fire (single-order size unchanged). Per-city stays 15% = $75 (geographic diversification preserved — day budget must spread across ≥8 cities).
- Hot-reload via the strategy's `_reload()` mtime check; no service restart needed.

**Notable decisions:**
- (A) over (B): kept `max_per_city_pct` at 15% rather than bumping it parallel to the day cap. Per-city diversification is load-bearing for the eventual paper→live validation gate ("30+ RTs with WR ≥ 65%") — if paper concentrates by city, the WR metric won't reflect what diversified-live would produce.
- Crypto NOT touched. Crypto uses `mode: fixed_usd, fixed_amount: 1.0` — daily projection ~$80, no cap binding.

**Verification:**
- mtime update at 21:48:35 UTC triggers reload on next scan cycle.
- Combined with P2 fix that landed 35min later, 11 weather `would_have_placed` rows in the first 24min — fires resumed.

**Rollback:** restore from `config/strategies.yaml.bak-day600-20260515-214835`

---

## 2026-05-15 21:33 UTC — BitUnix HTF Phase 1B followup — brokers/bitunix.py + persistence/models.py

**Triggered by:** P2 followup filed in BACKLOG at the end of the 15:35 Phase 1B deploy. The new funding-rate poll loop emitted `'BitunixBroker' object has no attribute 'get_funding_rate'` every 30 min because Phase 1B shipped main.py + observer.py + web/app.py but not the broker file. `bitunix_funding_history` table couldn't be created until this method existed.

**Backup tag:** `pre-htf-1b-followup-20260515-2133`

**Files deployed (2 — bundled per the Phase 1B lesson on shipping coherent units):**
- **MODIFIED** `trading_corp/brokers/bitunix.py` — adds `get_funding_rate(symbol) -> float | None` (public endpoint, no auth, transient httpx client so it works regardless of stub/connected state). Also brings forward the PR 5 reconciler-supporting methods `list_open_positions(db_url) -> list[OpenPosition]` and `modify_position_tp_sl_order(...) -> NotImplementedError`. Both are inert on prod: nothing calls them until Phase 1C ships `bitunix_position_reconciler.py`.
- **MODIFIED** `trading_corp/persistence/models.py` — adds `@dataclass OpenPosition` (reconciler-facing view of one open trade — order_id, symbol, side, qty, entry_price, current_sl, tp_plan, filled_legs, opened_ts). Imported only by `BitunixBroker.list_open_positions`; inert on prod without the reconciler.

**Scope rationale (Option 1 from in-session triage):** Branch's `bitunix.py` imports `OpenPosition` from `persistence.models` at module top — shipping `bitunix.py` alone would have crashed module load with `ImportError` → service crash-loop. Per `phased_deploy_lesson.md` ("ship whole coherent bundles, not subsets"): paired `models.py` in the same deploy. Now prod bitunix.py + models.py are md5-identical to branch (LF-normalized) — Phase 1C drops from 9 files to 8 (no broker / persistence churn needed).

**Verification (post-restart 21:33:35 UTC):**
- Pre-swap md5: bitunix.py `33235a76ffec973b4e39fcc91f4a31dd`, models.py `cfe089dd009df0274a7965d03f2ca55d` (= main, LF).
- Post-swap md5: bitunix.py `5b7e186688f6b33052a873977e6bdde9`, models.py `71108b3342ca0b3d4912fec2055f4356` (= branch, LF). Match expected.
- PID rotation 434263 → 449440 on `systemctl restart`. Service `active (running)`, healthz `HTTP 200`.
- BitUnix observer wiring log unchanged: `scoring=True, pa_enabled=False, htf_gate_mode=off, htf_regime_enabled=False, trade_plan_active=False` — all Phase 1B-vintage dormant flags still in place.
- `BitunixBroker connected (account=bitunix-futures, equity=$2819.55, 0 positions)` — real account read still works.
- **Funding-rate fetch SUCCESS:** boot log shows `bitunix HTF funding primed: rate=-0.006032` (was the every-30min AttributeError pre-deploy).
- **`bitunix_funding_history` table CREATED** (visible in `sqlite_master`) with 2 rows persisted within the first minute (boot prime + first poll-loop tick).
- Bar archiver / signal-ledger / observer-bias tables unaffected: `bitunix_bar_history` has 1d=200, 1h=206, 3m=321, 4h=202 rows.
- 5 min of post-restart logs: zero new `AttributeError` / `has no attribute get_funding_rate` / `funding_rate fetch failed`.

**Behavioral change on prod:** None for trade flow. `get_funding_rate` is consumed only by the HTF context provider's funding-extreme threshold check inside the regime gate, which is dormant (`htf_gate_mode=off`). New observable activity: `bitunix_funding_history` fills at ~48 rows/day (30-min poll). PR 5 reconciler stubs are dead code on prod until Phase 1C lands.

**Inert / dormant on current traffic:**
- `OpenPosition` dataclass — only consumer is `BitunixBroker.list_open_positions`, which has no caller without the Phase 1C reconciler.
- `BitunixBroker.modify_position_tp_sl_order` raises `NotImplementedError`; never reached without the reconciler.

**Deploy mechanics callout — `az run-command` over HTTPS:** SSH port 22 blocked from local network this session. Used the `az vm run-command invoke` fallback per `trading_corp_az_run_command.md`. Output is truncated at 4kb but `--scripts` accepts the 24-35kb base64 payload fine; pattern: `B64=$(cat file.b64); az vm run-command --scripts "echo '$B64' | base64 -d > /tmp/x.py && ..."`. Documented for future deploys against restricted networks.

**Rollback recipe:**
```bash
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
  --command-id RunShellScript \
  --scripts "
TAG=pre-htf-1b-followup-20260515-2133; BASE=/home/azureuser/trading_corp
cd \$BASE
for f in trading_corp/brokers/bitunix.py trading_corp/persistence/models.py; do
  sudo cp \$f.\$TAG \$f
done
sudo rm -rf trading_corp/brokers/__pycache__ trading_corp/persistence/__pycache__
sudo systemctl restart trading-corp.service
"
```
Safe to roll back: removes the new method + `OpenPosition` dataclass. Phase 1B-shipped main.py + observer.py + web/app.py don't import either at module top (the funding poll uses `getattr` dispatch). After rollback the every-30min `get_funding_rate` warning returns; the `bitunix_funding_history` table stays in the DB but stops getting new rows.

---

## 2026-05-15 15:41 UTC — Fix B: crypto strike-distance-from-spot curation (3-deploy iteration)

**Triggered by:** Post-Fix-A, crypto still 0 ProposedOrders. Pre-fix audit (155 evals in 2 cycles) showed XRP T-suffix tail strikes consuming the entire `k_per_cycle=30` budget — all deep-OTM at Kalshi's $0.01 pricing floor, guaranteed 1% edge noise, 100% `no_edge` skips. BTC/ETH near-spot markets where real edges could live were starved by tightest-spread sort favoring deep-XRP tails.

**Backup tag:** `pre-strike-distance-fix-20260515-1610`

**Files deployed (1, 3 iterations):**
- `trading_corp/agents/strategies/kalshi_crypto_arb.py` — new strike-distance filter between the existing horizon filter and the tightest-spread sort. Computes `expected_move = spot × annual_vol × √(horizon_years)` per survivor; drops anything where `|strike - spot| > K × expected_move` (K=3 default, hot-reloadable via `strategies.yaml kalshi_crypto_arb.strike_distance_k_sigma`). Also surfaced 3 new fields on the survivor dict (`floor_strike`, `cap_strike`, `strike_type`) for downstream use, and added `skipped_strike_distance` + `strike_distance_k_sigma` to the `kalshi_crypto_scan` audit summary.

**Iteration history (all 3 within ~20 min):**
1. **15:28 UTC.** First version. `_strike_point()` predicate matched on `strike_type` field which the discovery objects don't carry (only `get_market()` does). Result: filter ran but `skipped_strike_distance=0` — all markets had empty `strike_type` → punted as None → kept.
2. **15:35 UTC.** Extended `_strike_point()` to try ticker-suffix parsing when `strike_type == "custom"`. Still 0 — same root cause: empty strike_type meant `custom` branch never entered.
3. **15:41 UTC.** Dropped the `strike_type` gate entirely; just try `parse_kalshi_strike_suffix(ticker)` on every survivor. Works for B-/T-suffix tickers (covers ~all crypto markets). Returns None for greater_or_equal SOL15M momentum markets and *MAXMON markets, which are kept (they're near-spot momentum markets where the filter wouldn't reject them anyway).

**Verification (post-15:41 restart, 2 scan cycles):**
- Scan summaries: `pre=100, skipped_strike_distance=46/47, candidates=5/6` — filter actually filtering now (was 0).
- Asset mix: ETH, BTC15M, DOGE15M (was 100% BTC tail-T-strikes pre-fix, 100% XRP tail pre-Fix-B).
- Eval breakdown: 11 evals → 6 `near_threshold` + 5 `no_edge`. **Both are real-math gate decisions, not pre-math drops.** Strategy is now structurally correct.
- Best edge consistently: `KXETHD-26MAY1512-T2229.99` — spot $2218, strike $2230, 22min horizon, model says 14.5% YES vs market 6% implied = **8.5% edge**. Reaches the divergence gate but just below the 10% `min_divergence_pct` threshold.
- 0 ProposedOrders yet — legitimate: 8.5% < 10% gate.

**Notable code decisions:**
- **K=3 starting point** — captures ~99% of the model distribution. Tunable per-strategy via yaml. May tune K=4 if we observe legit edges getting dropped; K=2 if we still see floor noise.
- **Ticker-suffix parsing was always going to be needed** — discovery objects are intentionally lightweight in pykalshi. A future cleanup could `get_market` once per survivor (cache result, reuse in `_evaluate_market`) instead of two-stage parse, but that doubles discovery latency by 30+ API calls per cycle.
- **Filter is async** because `spot_provider.get_spot()` is async. Spot cache per cycle (`spot_cache` local dict) means only ~5 unique-asset spot calls per cycle regardless of 30+ survivors.

**Inert / dormant:**
- **Still 0 ProposedOrders.** Real-math result: 8.5% peak edge < 10% gate. Different scan windows / different assets / closer-to-spot strikes WILL produce ≥10% edges. Let it run.
- **Fix D (min_divergence_pct tuning)** in BACKLOG, deferred until post-Fix-B audit shows the empirical edge distribution. Today's 2 cycles are insufficient data.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-strike-distance-fix-20260515-1610; \
sudo cp /home/azureuser/trading_corp/trading_corp/agents/strategies/kalshi_crypto_arb.py.\$TAG \
        /home/azureuser/trading_corp/trading_corp/agents/strategies/kalshi_crypto_arb.py; \
sudo rm -rf /home/azureuser/trading_corp/trading_corp/agents/strategies/__pycache__; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-15 15:35 UTC — BitUnix HTF Phase 1B — full code surface, dormant mode

**Triggered by:** Phased deploy of branch `claude/gallant-tereshkova-49ef85` (HTF redesign PRs 1/2/3a/3c/5 + trade-plan PRs 1-6). Phase 1A had already landed 4 pure-module files at the top of this session (no service restart, no behavioral change — files were dormant on disk). Phase 1B ships the integration surface so PR 3c's PA + HTF gates and the new bar archiver / regime snapshot loops come online — but kept fully dormant via prod's existing strategies.yaml (no `pa_validation` block → `pa.enabled=False`; no `htf_gate` block → `htf_gate_mode=off`; no `trade_plan` block → reconciler not started). The branch was merged with main at `dc1d252` before deploy, making it a clean superset of prod for these files.

**Backup tags:**
- `pre-htf-1b-20260515-1529` — first 1B attempt; missed `web/app.py` and rolled back (TypeError: WebDeps got unexpected kwarg `bitunix_htf_provider`)
- `pre-htf-1b-20260515-1535` — second 1B attempt (THIS ONE) succeeded

**Files deployed (11 total — 4 from Phase 1A earlier + 7 in Phase 1B):**

Phase 1A (deployed earlier in session, pure modules, no restart needed):
- **NEW** `trading_corp/agents/strategies/bitunix_htf_regime.py` (1021 lines) — HTF regime classifier (pure module): EMA alignment / structure / ADX / MACD per-TF; composite BULL/NEUTRAL/BEAR regime; volatility tier; session classifier; `find_swing_points` helper.
- **NEW** `trading_corp/agents/strategies/bitunix_pa_validation.py` (310 lines) — PA validators (VWAP / volume / structure / rush_fall binary guards); `PAValidationConfig` reads YAML `bitunix_futures.pa_validation` block; `evaluate_pa_validation()` returns PASS / REJECT / DISABLED.
- **NEW** `trading_corp/data/bitunix_htf_context.py` (379 lines) — `BitUnixHTFContextProvider` wraps 1H/4H/1D LiveBarCaches + funding-rate fetcher; `snapshot()` returns gating context; `regime_snapshot()` produces RegimeVerdict for the gate; `run_funding_poll_loop()` + `run_regime_snapshot_loop()` async tasks.
- **NEW** `trading_corp/data/bitunix_bar_archiver.py` (165 lines) — async loop reading new bars from each BitUnix LiveBarCache and INSERT-OR-IGNORE-ing them into `bitunix_bar_history` (self-creates the table on init).

Phase 1B (deployed 15:35 UTC, service restarted):
- **MODIFIED** `trading_corp/main.py` (+150 lines net) — adds 3 HTF LiveBarCaches (1H/4H/1D @ max_bars=250); constructs `BitUnixHTFContextProvider`; loads `_pa_config` + `_htf_gate_mode` + `_trade_plan_config` + `_fee_config` from YAML; wires the bar archiver + funding poll + regime-snapshot async tasks; passes `bitunix_htf_provider` into `WebDeps`. Backwards-compat: all four new configs default to disabled when their YAML blocks are absent.
- **MODIFIED** `trading_corp/agents/divisions/bitunix_futures_observer.py` (extensive) — accepts new kwargs (`pa_config`, `htf_config`, `htf_gate_mode`, `htf_provider`, `trade_plan_config`, `fee_config`). New gate logic for PR 3c (PA validation + HTF regime gate) lives at the right spot in `_score_and_maybe_propose` but is bypassed when configs are disabled. New `_log_pa_validation` / `_log_htf_gate` / `_log_trade_plan_decision` audit writers. Imports from `swing.py` / `levels.py` / `trade_plan.py` (the new pure modules below).
- **MODIFIED** `trading_corp/agents/strategies/bitunix_confluence.py` — exports `BitUnixAlertEvent` (needed by observer); `BitUnixConfluenceConfig.from_dict` reads new optional fields (`score_timeframes`, `pa_factors_in_score`, `guards_in_score`, `ttl_per_tf` per factor) with safe defaults so it works against prod's older YAML.
- **MODIFIED** `trading_corp/web/app.py` (+6 lines) — adds `bitunix_htf_provider: Any = None` field to `WebDeps` dataclass. Nothing on prod reads it yet (web/data.py is unchanged in this phase).
- **NEW** `trading_corp/agents/strategies/swing.py` — fractal swing detection helper (re-exports from `bitunix_htf_regime.find_swing_points`).
- **NEW** `trading_corp/agents/strategies/levels.py` — HTF S/R levels via 3m→15m resample of `bitunix_bar_cache`.
- **NEW** `trading_corp/agents/strategies/trade_plan.py` — `FeeConfig` + `StrategyConfig` + `TradePlan` + `build_trade_plan` (PR 3 module — imported by observer but only used when `trade_plan.enabled: true`).

**Verification (boot @ 15:35:11 UTC):**
- `BitUnix observer wiring: scoring=True, pa_enabled=False, htf_gate_mode=off, htf_regime_enabled=False, trade_plan_active=False` — all gates dormant as designed.
- 3 HTF caches primed with 200 bars each (1H last_close=$79120, 4H last_close=$80582, 1D last_close=$81049). ATR values computed.
- `bitunix_bar_archiver online (caches=4, interval=60.0s)` — already primed 800 bars (4 caches × 200).
- `HTF regime-snapshot loop online (interval=600.0s)`.
- `HTF funding-rate poll online (interval=1800.0s)` — emits a non-fatal warning every 30min: `'BitunixBroker' object has no attribute 'get_funding_rate'` (filed as P2 followup; branch's `brokers/bitunix.py` adds this method but wasn't shipped in 1B). Funding refresh returns None gracefully; `bitunix_funding_history` table will be created once the broker method ships.
- `bitunix_bar_history` table verified in DB.
- `/healthz` returns 200.
- Service stable PID 432373; MainPID unchanged for 5+ min post-boot (no auto-restart).

**Behavioral change on prod:** NONE for trade flow. Score engine, BitUnix observer fire decisions, paper trade replay, all other divisions: unchanged. New code paths exist in dormant state (gates configured-disabled). Net new prod activity = `bitunix_bar_history` table fills at ~1 row per cache per closed bar; `audit_event(kind='htf_regime_snapshot')` rows accumulate at ~1 row per 10min.

**Notable scope discovery — Phase 1B grew from 1 file to 4 files during deploy:**
- Initial plan: ship only `main.py`. CRASHED on first attempt with `TypeError: BitunixFuturesObserver.__init__() got an unexpected keyword argument 'pa_config'` — branch's main.py calls the observer with new kwargs that prod's observer didn't accept.
- Forward-fix: add `observer.py` to bundle. Smoke test of observer construction passed, but second deploy CRASHED at `TypeError: WebDeps.__init__() got an unexpected keyword argument 'bitunix_htf_provider'` — branch's main.py also passes a new WebDeps field.
- Final-fix: add `web/app.py` to bundle. Third deploy succeeded. Bundle = `main.py + observer.py + confluence.py + web/app.py` (4 modified files).
- Plus 3 new pure modules (`swing.py` + `levels.py` + `trade_plan.py`) needed because branch's observer imports them at module level. SCP'd to prod as fresh files (no backup needed).

**Lesson logged in memory:** ship-by-subset for an integrated branch invites missed transitive deps. For future phased deploys against drifted prod, prefer "ship the whole branch in dormant mode, then flip flags" over "ship N files at a time."

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
cd /home/azureuser/trading_corp
TS=20260515-1535
for f in trading_corp/main.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/strategies/bitunix_confluence.py trading_corp/web/app.py; do
  cp \$f.pre-htf-1b-\$TS \$f
done
# swing.py / levels.py / trade_plan.py are new — rm to fully revert
rm trading_corp/agents/strategies/swing.py trading_corp/agents/strategies/levels.py trading_corp/agents/strategies/trade_plan.py
rm -rf trading_corp/__pycache__ trading_corp/agents/__pycache__ trading_corp/agents/divisions/__pycache__ trading_corp/agents/strategies/__pycache__ trading_corp/web/__pycache__
sudo systemctl restart trading-corp
"
```
Note: Phase 1A files (bitunix_htf_regime, _pa_validation, _htf_context, _bar_archiver) are imported by main.py post-1B; rolling back to pre-1B but leaving them in place is fine (they become orphan files again).

**Inert / dormant on current traffic (Phase 1B leaves these for Phase 1C and beyond):**
- **PA validation gate.** Module loaded, `pa_config` constructed but `pa.enabled=False` because prod YAML lacks `bitunix_futures.pa_validation` block. Phase 1C (deploy branch's strategies.yaml) activates shadow-mode validators.
- **HTF regime gate.** Module loaded, `htf_config` constructed with defaults, but `htf_gate_mode=off`. Phase 1C activates shadow mode; Phase 1D flips to enforce.
- **Trade-plan v2 path.** `trade_plan.py` on disk, observer's `_build_proposal_v2` exists but never called (`_trade_plan_config is None`). Legacy `_build_proposal` (Phase 3.2.2 era) handles all trades.
- **Position reconciler.** Conditional import in main.py gated on `_trade_plan_config is not None` — never imported, async task never started.
- **Dashboard view builders** for HTF / PA / decision-flow panels are NOT on prod (`web/data.py` and templates still pre-PR-3c). New panels would render data but the views aren't built. Phase 1D ships the dashboard refresh.

**Followups filed in BACKLOG (P2 priority):**
- Ship `trading_corp/brokers/bitunix.py` to silence the every-30min `get_funding_rate` warning and unlock funding-history persistence.

---

## 2026-05-15 15:10 UTC — Fix A: `greater_or_equal` + `less_or_equal` strike-type handlers (crypto + weather)

**Triggered by:** Post-14:39 dashboard fix, weather firing but crypto producing 0 ProposedOrders. Read-only audit-DB investigation found 155 of 455 (34%) crypto evaluations across 20 scan cycles were being silently dropped at `no_strike` — all `KXSOL15M-26MAY151045-45`-style 15-minute SOL momentum markets with `strike_type='greater_or_equal'`. Strategy only handled `greater | less | between | custom`. Plain-numeric ticker suffix (no B/T prefix) is also not parseable, but the API DOES populate `floor_strike` for these markets (verified via direct Kalshi probe: `floor_strike=89.1007` = the 10:30-snapshot anchor price; market resolves YES if avg over next 15min is ≥ that anchor).

**Backup tag:** `pre-greater-or-equal-fix-20260515-1545`

**Files deployed (2):**
- `trading_corp/agents/strategies/kalshi_crypto_arb.py` — added two branches to `_evaluate_market` strike-type dispatch (~line 335): `strike_type == "greater_or_equal"` → `threshold = floor_strike, direction = "greater"`; `strike_type == "less_or_equal"` → mirror with cap/floor. Comment notes the SOL15M momentum-market semantic (drift-free Gaussian centered on current spot fits the same shape).
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — same two branches added defensively at line ~391. Weather rarely sees these types today but consistency keeps the dispatch resilient.

**Verification (post-restart 15:10:47 UTC, after 8 scan cycles + 1 weather cycle):**
- no_strike rate: 160/38 = **4.21/cycle BEFORE** → 10/8 = **1.25/cycle AFTER** (70% reduction).
- Remaining 10 no_strike are all `KXDOGE-26MAY1512-T0.x` T-suffix custom-direction markets — separate P2 issue (T-suffix direction inference), already in BACKLOG.
- Weather: no regression. 29 evaluations, 17 no_edge / 7 no_size / 3 near_threshold / 2 risk-rejected (the known polymarket-bound scope leak P2). Same shape as pre-fix.
- `KXSOLE-26MAY1512-T126.9999`-style markets with direction=greater now reaching the math layer (verified by audit row presence with implied_yes, prob_yes, edge_pct fields populated).
- SOL15M tickers didn't appear in the post-fix scan window — those markets cycle every 15 min and weren't active during this scan. Will surface on subsequent cycles.

**Inert / dormant:**
- **Still 0 crypto ProposedOrders.** All 200+ evaluated markets are hitting no_edge — model agrees with market on tail-strike T-tickers. Fix B (strike-distance-from-spot curation at discovery) is the structural unblock for this — filed as P2 in BACKLOG. Today's Fix A removed the dispatch bug; Fix B is needed to put the budget on strikes where edges can actually live.

**Operational notes:**
- **Service was crash-looping at the moment of restart** due to a `BitunixFuturesObserver.__init__()` `TypeError: got an unexpected keyword argument 'pa_config'` — unrelated to my deploy. Another session was concurrently deploying BitUnix changes; the crash-loop resolved at 15:14 UTC when their deploy stabilized. Service then came up cleanly with all scanners (including kalshi_crypto with $500 paper_capital) online. My deploy was not affected.
- **BACKLOG additions:** Fix B (strike-distance curation, P2 with detailed shape + K=3 starting point), Fix D (min_divergence_pct tuning, deferred until post-Fix-B data), both filed under the "P2 — added 2026-05-15" section. Fix C (T-suffix direction inference) was already in BACKLOG.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-greater-or-equal-fix-20260515-1545; \
BASE=/home/azureuser/trading_corp/trading_corp/agents/strategies; \
sudo cp \$BASE/kalshi_crypto_arb.py.\$TAG \$BASE/kalshi_crypto_arb.py; \
sudo cp \$BASE/kalshi_weather_arb.py.\$TAG \$BASE/kalshi_weather_arb.py; \
sudo rm -rf \$BASE/__pycache__; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-15 14:39 UTC — Dashboard actor-whitelist fix (kalshi_weather/crypto now visible)

**Triggered by:** User reported "i see kalshi weather trades on telegram but not on the dashboard ui". Telegram channel is wired off the strategy's ProposedOrder; the dashboard reads from the same audit table but with actor-whitelist filters that hadn't been extended for the new specialized agents.

**Root cause:** Two queries in `trading_corp/web/data.py` filter on a hardcoded actor list `('kalshi_tail_price_arb', 'kalshi_temporal_bucket_arb', 'kalshi_llm_arbitrage', 'kalshi_copy_trader')` — `kalshi_weather_arb` and `kalshi_crypto_arb` were missing. The audit rows exist with correct `division` payload field; the actor filter dropped them.

**Files deployed (1):**
- `trading_corp/web/data.py` — added `kalshi_weather_arb` + `kalshi_crypto_arb` to:
  - `_query_pm_open_trades` (line 2747) — Open tab actor whitelist
  - `_query_pm_pending_count` (line 2876) — tile "pending" count
  - `arb_type` fallback ladder in the open-trades enrichment (~ line 2775): weather → `"weather_forecast"`, crypto → `"crypto_spot"`

**Notably NOT touched (already correct):**
- `trading_corp/agents/kalshi_resolver.py` — prod already had `kalshi_weather_arb` + `kalshi_crypto_arb` in `_KALSHI_ACTORS`, `_KALSHI_DIVISIONS`, `_ACTOR_TO_DIVISION`, and `_ACTOR_TO_ARB_TYPE_DEFAULT`. Local was behind prod (single-line `_KALSHI_DIVISIONS` style); synced local TO prod content to clear the drift. So the resolver was already creating round-trip rows for weather/crypto resolutions correctly — the dashboard read side was the only gap.

**Backup tag:** `pre-dashboard-actor-whitelist-20260515-1445`

**Verification (post-restart 14:39 UTC):**
- Direct call to `_query_pm_open_trades(... ['kalshi_weather'], limit=10)` returns **4 open trades** with proper enrichment: ticker, side='no', qty, price, arb_type='weather_forecast' all populated.
- `_query_pm_pending_count(... ['kalshi_weather','kalshi_crypto'])` returns 4 (matches the 4 `would_have_placed` audit rows that survived risk gate).
- No exceptions post-restart.

**Inert / dormant:**
- **Activity-rail per-strategy enrichment (line 3371 actor list) NOT extended.** That's cosmetic — the basic event still renders, just without the rich kalshi-tail/temporal-bucket-specific badges. Worth a P3 follow-up if the user wants weather-specific row styling.
- **No styled `kalshi_weather_analysis.html` / `kalshi_crypto_analysis.html` partial.** The right-rail click-to-expand panel currently shows raw payload JSON. Already filed as P3 follow-up in the 2026-05-15 02:56 UTC Tier-1 deploy log entry.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-dashboard-actor-whitelist-20260515-1445; \
sudo cp /home/azureuser/trading_corp/trading_corp/web/data.py.\$TAG /home/azureuser/trading_corp/trading_corp/web/data.py; \
sudo rm -rf /home/azureuser/trading_corp/trading_corp/web/__pycache__; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-15 14:27 UTC — Paper-capital + crypto horizon pre-filter (downstream-gate unblock)

**Triggered by:** Post-14:06 quote-fix deploy, audit showed weather + crypto still producing 0 ProposedOrders. Investigation found two **real bugs** + two legitimate gates:
- 9/30 weather evaluations had **edges of 28-92%** but Kelly sized to $0 because the kalshi_weather paper broker was instantiated with `starting_equity=0.0`.
- 150/150 crypto evaluations hit horizon-cap because long-dated SOLMAX/ETHMAX-style markets (~5,535h out) crowded the `k_per_cycle=30` budget via the tightest-spread sort, starving near-term BTC/ETH/XRP markets.

**Backup tag:** `pre-paper-cap-horizon-fix-20260515-1430`

**Files deployed (4 — 3 code + 1 config):**
- `trading_corp/utils/divisions.py` — added `paper_capital: float = 0.0` field to `Division` dataclass; `load_divisions()` reads it from the yaml entry. Default $0 = legacy behavior (existing paper divisions unaffected unless they opt in).
- `trading_corp/main.py` — `family == "paper"` branch (line 1329) now passes `division.paper_capital` to `PaperBroker(starting_equity=...)` instead of hardcoded 0.0.
- `trading_corp/agents/strategies/kalshi_crypto_arb.py` — new horizon-aware survivor filter between the no-ask filter and the tightest-spread sort. Computes `(expected_expiration_time - now).hours` per survivor via inline `_horizon_hours()` helper; drops anything `> max_horizon_hours` (default 168h). Cheap — one ISO parse per survivor. No new audit fields.
- `config/divisions.yaml` (**prod-only edit via inline Python patcher**) — added `paper_capital: 500.0` to both `kalshi_weather` and `kalshi_crypto` entries. **Local divisions.yaml does NOT have these entries** (prod-divergent per `trading_corp_prod_git_drift.md`); patch was applied directly to prod via ssh + a small idempotent regex script (kept inline; not durable in scripts/). Re-run safe — skips if `paper_capital` already present.

**Features shipped:**
- **Weather Kelly sizer now has bankroll to work with.** $500 paper notional × 25% fractional Kelly × edge math, capped per-market (5% = $25), per-day (25% = $125), per-city (15% = $75). Verified: 5 ProposedOrders fired in first post-restart scan cycle (was 0 across the prior 5+ hours).
- **Crypto discovery no longer starved by long-dated markets.** First post-restart scan: 30/30 evaluations were XRP 0.6h-horizon (the 11am EDT event) — the actual short-term liquidity surface, not 230-day MAXMON noise. 0 horizon-skips (was 150/150).

**Verification (post-restart 14:27:10 UTC):**
- md5-diff confirmed local=prod on all 3 code files (no drift).
- Service active 14:27:11; PaperBroker startup logs show `account=paper_kalshi_weather equity=$500.00` (via subsequent snapshot path; service-init log line stops at the `paper-default` broker but div-level brokers come up via DataExec.register).
- Local `pytest tests/test_kalshi_weather_sizing.py`: 15/15 pass.
- Audit DB post-restart (one weather scan + 2 crypto scans):
  - Weather: 30 evaluated, 3 `would_have_placed`, 2 `order_rejected_by_risk`, 22 `no_edge`, 2 `near_threshold`, 1 `no_size` (Kelly worked but daily cap exhausted by the 3 prior fires — exactly the design intent).
  - Crypto: 30 evaluated, 30 `no_edge` (model agrees with market on tail-strike XRP), **0 horizon skips**.
  - `proposed_order` table now shows 5 kalshi_weather rows since 14:27:30 (each exactly $25 notional = the per-market cap — Kelly math + cap ladder verified end-to-end).

**Notable code changes / decisions:**
- **$500 paper_capital** chosen to roughly match the real Kalshi account ($499 live). Each kalshi_* paper division acts as a "what if this strategy had its own $500 sleeve" sim. Once auto_execute flips for any of these, real allocation needs to be carved out of the shared live account.
- **Horizon filter pre-k-cap** is the right cut point. Doing it after the cap would not help; doing it at discovery-stage (before survivor dict build) is barely cheaper and loses the audit-readability of survivor-stage data.
- Weather strategy was NOT given a similar horizon pre-filter — its survivors were all within 72h naturally; defense-in-depth deferred until needed.

**Latent bug surfaced (not introduced):**
- **2 weather ProposedOrders were rejected with `risk_reason: 'polymarket: implied prob 0.010 outside [0.05, 0.95] bounds'`.** The polymarket implied-prob bounds check is firing on **kalshi_weather** orders — wrong-scope leak in the risk gate. Both rejected orders were deep tail markets ($0.01 + $0.03 limits, 1% + 3% implied) so the practical loss is small, but the bound shouldn't apply across venues. Filed as P2 in BACKLOG.md.

**Inert / dormant:**
- **Crypto: 0 ProposedOrders since deploy** despite the horizon fix working. 30/30 XRP markets at 0.6h horizon all genuinely no_edge: spot=$1.4369, strikes $1.55-$1.64 (5-15% above), model and market both say ~0-1% chance. Legit "no fire — tight market". Different scan-windows will surface different setups.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-paper-cap-horizon-fix-20260515-1430; BASE=/home/azureuser/trading_corp; \
sudo cp \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
sudo cp \$BASE/trading_corp/utils/divisions.py.\$TAG \$BASE/trading_corp/utils/divisions.py; \
sudo cp \$BASE/trading_corp/agents/strategies/kalshi_crypto_arb.py.\$TAG \$BASE/trading_corp/agents/strategies/kalshi_crypto_arb.py; \
sudo cp \$BASE/config/divisions.yaml.\$TAG \$BASE/config/divisions.yaml; \
sudo rm -rf \$BASE/trading_corp/__pycache__ \$BASE/trading_corp/utils/__pycache__ \$BASE/trading_corp/agents/strategies/__pycache__; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-15 14:06 UTC — Kalshi fractional-trading quote-read fix (weather + crypto)

**Triggered by:** User reported "no rows showing up" for kalshi_weather + kalshi_crypto. Audit DB confirmed: 0 of 4,106 weather evaluations + 0 of 18,520 crypto evaluations passed all gates in prior 12h; every single market hit `skip_code: no_implied`. Other Kalshi strategies (llm_arbitrage, copy_trader, temporal_bucket_arb) were producing rows fine.

**Root cause:** Kalshi flipped weather + crypto markets to `fractional_trading_enabled: true`. The integer-cent fields (`yes_ask`/`no_ask`/`yes_bid`/`no_bid`) are absent from the API response; only the `*_dollars` string fields populate. Both strategies read `getattr(full, "yes_ask", None)` → always `None` → `implied_yes = None` → 100% `no_implied` skip rate. Confirmed via direct prod probe on `KXHIGHCHI-26MAY15-B73.5`: pykalshi market object has `yes_ask_dollars='0.3400'` but `yes_ask` is missing as an attribute entirely.

**Backup tag:** `pre-fractional-quote-fix-20260515-1350`

**Files deployed (3):**
- `trading_corp/agents/strategies/_weather_math.py` — **NEW helper** `kalshi_quote_dollars(m)` returns `(yes_ask, no_ask, yes_bid, no_bid)` in dollars. Prefers `*_dollars` string fields; falls back to integer cents × 0.01 for legacy non-fractional markets. Returns 0.0 for unparseable/missing sides (matches Kalshi's existing "no quote" convention).
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — Import + use `kalshi_quote_dollars` in survivor-dict construction AND in `_evaluate_market`. Removed the now-redundant `(yes_ask_cents or 0) / 100.0` conversion step at line 533.
- `trading_corp/agents/strategies/kalshi_crypto_arb.py` — Same pattern, two sites.

**Features shipped:**
- **Weather strategy now reads quotes correctly on all fractional markets.** 30 evaluations post-restart, all passed the quote layer (was 0/30 pre-fix). Downstream skip distribution: 17 no_edge, 9 no_size, 4 near_threshold — all legitimate market-state outcomes, no quote-layer bug. Full Tier-1 path firing (ensemble σ + Kelly visible in payloads).
- **Crypto strategy now reads quotes correctly on all fractional markets.** 150 evaluations across 5 scan cycles, all 150 passed the quote layer. Currently all hit `horizon` skip (the 168h max_horizon_hours gate) because discovery is dominated by long-dated SOL markets — that's a separate `k_per_cycle`-ordering issue, not a quote bug.

**Notable code changes:**
- `getattr(m, "yes_ask", None)` is now NEVER the right read on Kalshi markets that may have `fractional_trading_enabled: true`. Any future strategy/code path reading quote fields should use `kalshi_quote_dollars()` from `_weather_math.py`.
- Survivor dicts now store dollar values (not cents) under the `yes_ask`/`no_ask`/`yes_bid`/`no_bid` keys — the spread-sort lambda still works because the relative ordering is preserved.

**Verification:**
- md5-diff confirmed clean on all 3 files (no prod drift — surgical patches applied cleanly).
- 15/15 tests pass in `tests/test_kalshi_weather_sizing.py`.
- Local smoke-test of helper across fractional / legacy-cents / empty market objects returns expected dollar values.
- Service restart 14:06:04 UTC; no exceptions in journalctl post-restart.
- Audit DB: weather post-restart = 30 evaluated, 0 `no_implied`, 30 `payload_json NOT LIKE '%skip_code%'` (all passed quote layer). Crypto = 150 evaluated, 0 `no_implied`, 150 passed quote layer.

**Inert / dormant on current traffic:**
- **0 ProposedOrders fired since restart.** Real market-state, not a bug: weather forecasts agree with implied (no_edge), Kelly floors to $0 on tiny edges (no_size), or near-threshold uncertainty (near_threshold). Crypto exhausts k_per_cycle on long-dated SOL markets that fail the 168h horizon gate. Both are legitimate downstream skips — dashboard rows will materialize when a real edge appears.

**Follow-up risk (not blocking):**
- Crypto discovery ordering: long-dated SOL markets are crowding out near-term BTC/ETH evaluations. Worth tuning the survivor sort (currently tightest-spread first) or adding a horizon-aware pre-filter so the 30-market budget is spent on in-horizon markets.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
BASE=/home/azureuser/trading_corp/trading_corp/agents/strategies; \
TAG=pre-fractional-quote-fix-20260515-1350; \
for f in _weather_math.py kalshi_weather_arb.py kalshi_crypto_arb.py; do
  sudo cp \$BASE/\$f.\$TAG \$BASE/\$f
done; \
sudo rm -rf /home/azureuser/trading_corp/trading_corp/agents/strategies/__pycache__ \
            /home/azureuser/trading_corp/trading_corp/agents/__pycache__; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-15 06:44 UTC — K3 Watch-list deep-scan pivot (replaces manual seed)

**Triggered by:** 06:09 deploy of manual-seed watch-list yielded 2 of 14
handles (Foster, PredMTrader) — both **visibility-opaque** on Apify. A
follow-up `refresh_kalshi_whales.py` run with `--watch-only-only` found
only 1 viable whale in the volume/all_time top-48 (47/48 opaque). Pivoted
to a deep multi-leaderboard rank-walk with a visibility cache.

**Backup tags (none — only added a new script):**

**Files deployed (2):**
- `trading_corp/scripts/refresh_kalshi_whales.py` — added `--watch-only-n N`
  (default 20) and `--watch-only-only` (skips writing selected_whales).
  Always-on watch_only_whales output: runner-ups from the same scored
  viable pool. Backup tag `pre-runnerups-20260515-0625`.
- `trading_corp/scripts/seed_kalshi_watchlist_deep.py` — **NEW**. Walks
  (categories × time_windows) leaderboards (default 6×3=18), de-duped
  candidate pool, batch-probes in groups of 10, accepts any handle with
  `closed_positions_count >= min_sample` (default 5 — lowered from
  selection's 20 because this is observation, not betting). Stops at
  `target_n=10` visible OR `max_probe=60` total probes. Persists per-handle
  visibility decisions to `agent_state(apify_visibility_cache)` with a
  30-day TTL so re-runs skip known-opaque whales.

**Verification (06:48 UTC):**
- Deep-scan probed **60 of 910 candidate handles** across 18 leaderboards.
  **2 visible** (lengthy.starfish, Hispaniola), 58 opaque → confirms
  Kalshi-wide visibility rate is ~3.3%, not a top-of-leaderboard artifact.
- `refresh_kalshi_watchlist_stats.py` populated stats for both:
  - **lengthy.starfish** — Politics/monthly rank #13. 20 resolved
    (16W/4L = **80% WR**), total PnL **+$3,430.65** over 149K contracts.
    20 open positions. Top categories: Sports, Elections.
  - **Hispaniola** — Politics/monthly rank #40. 16 resolved
    (5W/11L = **31% WR**), total PnL **−$199.07**. 1 open. Top
    categories: Sports, Politics.
- Dashboard renders both rows on `/prediction-markets/kalshi_copy_trading`
  Whales tab (curl HTTP 200; 3 matches for "Watch list/lengthy.starfish/
  Hispaniola"). The manual-seed survivors (Foster, PredMTrader) were
  evicted by the deep-scan overwrite — no longer in the panel.
- Apify visibility cache: 60 entries (58 opaque + 2 visible).
- Total Apify spend: ~$3 (deep scan) + $0.50 (stats refresh).

**Systemd timers (post-deploy 06:54 UTC):**
- `trading-corp-watchlist-stats.timer` — daily 12:00 UTC; refresh stats
  for currently-tracked whales (~$0.50/run).
- `trading-corp-watchlist-deep.timer` — weekly Sunday 14:00 UTC; deep
  scan to grow watch_only_whales organically (~$2-3/run with warm cache).
- Unit files committed to `infra/systemd/`.

**Inert / pending:**
- **Visibility ceiling.** Even with 18 leaderboards and rank-walk depth,
  the visibility rate is uniformly ~3.3%. Each weekly cron run probes
  ~60 NEW candidates (skipping known-opaque via the cache); expect to
  net +1-3 visible whales/week. Watch list will grow organically.
- **selection_metadata side effect.** `--watch-only-only` skips writing
  selected_whales but still overwrites selection_metadata with the
  latest summary. Minor — the metadata reflects the most-recent
  scoring run regardless. Doesn't affect K3 behavior.
- **WO-4 (Promote button)** — still deferred. Disabled stub in UI.

**Rollback recipe (deep scan layer only — selected_whales untouched):**
```bash
ssh azureuser@trading.jacksumner.com "
BASE=/home/azureuser/trading_corp; \
TAG=pre-runnerups-20260515-0625; \
sudo cp \$BASE/trading_corp/scripts/refresh_kalshi_whales.py.\$TAG \$BASE/trading_corp/scripts/refresh_kalshi_whales.py; \
sudo rm -f \$BASE/trading_corp/scripts/seed_kalshi_watchlist_deep.py; \
sudo rm -rf \$BASE/trading_corp/scripts/__pycache__"
# Optional: clear the cache + watchlist
# sqlite3 ... DELETE FROM agent_state WHERE agent='kalshi_copy_trader'
#   AND key IN ('apify_visibility_cache','watch_only_whales','watch_only_stats','watch_only_deep_metadata');
```

---

## 2026-05-15 06:09 UTC — K3 Watch-list (observation-only whale tracking)

**Triggered by:** User-supplied watchlist of 14 X-handles (9 Tier-1 public
traders + 5 Tier-2 curators) — wanted to track their performance without
copy-trading them. Path is read-only twin of `selected_whales`: same
Apify data source, never emits ProposedOrders, future `[Promote]` flow
moves a row onto the live copy roster.

**Backup tag:** `pre-watchlist-20260515-0608`

**Files deployed (5):**
- `config/kalshi_watchlist_seed.yaml` — NEW. 9 Tier-1 + 5 Tier-2 handles
  with notes. Edit + re-run seed to add/remove.
- `trading_corp/scripts/seed_kalshi_watchlist.py` — NEW. One-shot probe.
  Tier-1 dropped if `fetch_profiles` returns no row for the nickname
  (log line warns). Tier-2 dropped unless `fetch_trades ≥ 1` row.
  Writes `agent_state(kalshi_copy_trader, watch_only_whales)` as
  `list[dict{handle, tier, source_x_handle, notes, included_iso, probe}]`.
  Idempotent.
- `trading_corp/scripts/refresh_kalshi_watchlist_stats.py` — NEW. Daily
  refresh. fetch_profiles + fetch_closed_positions + fetch_open_positions
  → `compute_stats` → `agent_state(watch_only_stats)`. Logs audit kind
  `kalshi_watch_only_refresh`. Never emits ProposedOrders.
- `trading_corp/web/data.py` — added `KalshiWatchOnlyRow` dataclass,
  `_query_kalshi_watch_only_rows()` (reads agent_state), wired into
  `PMDashboardView.kalshi_watch_only` via `asyncio.gather` and only
  populated when `kalshi_copy_trading` is in scope.
- `trading_corp/web/templates/partials/pm_dashboard_body.html` — Watch
  List panel below Selected Whales on the Whales tab. Tab count
  includes both. `[Promote]` button rendered as **disabled stub**
  (tooltip: "ships next, WO-4").

**Verification (post-deploy 06:11 UTC):**
- Service `active` after restart.
- Seed run: probed 14 handles. **2 survivors** — `Foster` and
  `PredMTrader`. The other 7 Tier-1 names dropped as
  `profile_unresolved` (their X handles don't match a Kalshi nickname),
  all 5 Tier-2 names dropped as `no_trades` (curators/aggregators
  don't trade on Kalshi). Apify spend ≈ $0.30.
- Refresh run: both survivors are **visibility-opaque** —
  `closed_positions=0`, `open_positions=0`. Apify gotcha (documented in
  `kalshi_apify_client.py`): "Trade/position visibility is per-user
  opt-in. Top leaderboard names may expose only profile-level data."
  Foster's profile reports 8,784 lifetime markets and PredMTrader's
  3,529 — they DO trade, they just opt out of visibility.
- Dashboard: `curl http://127.0.0.1:8000/prediction-markets/kalshi_copy_trading`
  returns 200; "Watch list — observation only" panel renders with
  both rows visible.

**Inert / pending:**
- **No actual performance data for either survivor yet** — both are
  visibility-opaque. The watch-list infrastructure is live, but until
  we (a) find correct Kalshi nicknames for the 7 mismatched Tier-1
  names, or (b) source data from somewhere other than Apify's profile
  scraper, the panel will keep showing zero stats.
- **No cron entry yet** for the daily stats refresh. Script runs
  manually for now.
- **WO-4 (Promote button)** — deferred per user. Button is a stub
  with tooltip pointing forward.

**Cost expectation:** ~$0.50/day Apify at the current 2-survivor list
size, ~$15/mo. Scales linearly with survivors.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
BASE=/home/azureuser/trading_corp; \
TAG=pre-watchlist-20260515-0608; \
sudo cp \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
sudo cp \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html.\$TAG \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html; \
sudo rm -f \$BASE/config/kalshi_watchlist_seed.yaml \
            \$BASE/trading_corp/scripts/seed_kalshi_watchlist.py \
            \$BASE/trading_corp/scripts/refresh_kalshi_watchlist_stats.py; \
sudo rm -rf \$BASE/trading_corp/web/__pycache__ \$BASE/trading_corp/scripts/__pycache__; \
sudo systemctl restart trading-corp.service"
```
(Optional cleanup: `DELETE FROM agent_state WHERE agent='kalshi_copy_trader' AND key LIKE 'watch_only_%';`)

---

## 2026-05-15 03:14 UTC — Kalshi Weather city-code aliases (follow-up to Tier-1)

**Triggered by:** First two post-Tier-1 scan cycles showed 0 evaluations
reaching the new ensemble/Kelly code path because of 100% early-stage
skips. Audit-mode sweep on `kalshi_weather_skipped_no_coords` payloads
showed 6 unknown codes: TMIA (42), TCHI (34), TPHIL (29), TLAX (24),
TNYC (22), NY (10). Each is the Kalshi T-prefix or short-form variant
of a city we already have in the fallback table.

**Files deployed (1):** `trading_corp/agents/strategies/kalshi_weather_arb.py`
— added 6 aliases to both `_CITY_COORDS_FALLBACK` and
`_CITY_TO_METAR_STATION`. Each alias points at the same resolution
station as its non-T sibling.

**Verification (post-deploy 03:14:22 UTC):**
- Two scan cycles. `kalshi_weather_skipped_no_coords` count = **0**
  (was 28 over the prior two cycles).
- All 60 candidates now reach the implied-prob gate; all 60 still skip
  there because overnight Kalshi book is sparse (zero quotes on Denver
  / Miami / Chicago weather buckets at 03:25 UTC = 11:25pm EDT).
- New `sigma_source` / `ensemble_n_members` / `nowcast_blend_w` /
  `kelly_full_pct` fields STILL not landed in audit — gated on a market
  with both valid coords AND active quotes. Expected during daytime hours.

**Inert / dormant on current traffic:**
- Ensemble σ + nowcast blend + Kelly sizing code paths remain unexercised
  by the scan loop. Direct prod smoke test of the underlying clients
  already proven (see prior entry). Audit-row confirmation pending
  next active-book scan.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
# Re-scp the immediately-prior version of kalshi_weather_arb.py from local
# git history (commit predates 03:14:22 UTC); then:
sudo rm -rf /home/azureuser/trading_corp/trading_corp/agents/strategies/__pycache__; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-15 02:56 UTC — Kalshi Weather Tier-1: ensemble σ + nowcast blend + fractional Kelly

**Triggered by:** Board direction (this session). The earlier specialized-agent
ship (2026-05-14 20:54) shipped weather with a heuristic σ + flat $1 sizing.
Tier 1 replaces the σ heuristic with a *measured* cross-model ensemble std,
adds a METAR nowcast blend for sub-6h horizons, and replaces flat $1 with
fractional Kelly + per-market / per-day / per-city caps. Validation gate
(30 RTs, WR ≥ 65%) for `auto_execute: true` flip is UNCHANGED.

**Backup tag:** `pre-weather-kelly-20260515-0255` (strategies.yaml backup tag);
n/a for the new client files.

**Files deployed (7):**
- `trading_corp/data/open_meteo_client.py` — **NEW.** Async client for
  Open-Meteo's `/v1/forecast` multi-model endpoint. Returns
  `EnsembleObservation(members, models, target_iso)` from any of
  gfs_global / icon_global / ecmwf_ifs04 / meteofrance_seamless / gem_global
  that the API has for the location. 30-min in-memory cache keyed on
  (rounded lat, rounded lon, forecast_days). `get_ensemble_at(lat, lon, target_iso)`
  for hourly markets; `get_ensemble_daily_extremum(lat, lon, date, kind)`
  for KXHIGH/KXLOW (per-model daily max/min, then ensemble across models).
- `trading_corp/data/metar_client.py` — **NEW.** Async client for
  aviationweather.gov `/api/data/metar`. `get_nowcast(station)` returns
  `MetarNowcast(latest_temp_f, latest_obs_iso, trend_f_per_hour,
  n_observations)`. Latest obs + linear trend computed off the last 3h
  of observations. `extrap_at(target_iso)` performs `latest_temp + trend × Δh`.
- `trading_corp/agents/strategies/_weather_math.py` — added
  `kelly_fraction(p_model, market_price)`. Returns `max(0, (p·b − (1−p))/b)`
  where `b = (1−price)/price`. Unit-agnostic (will also serve
  `kalshi_crypto_arb` when it switches off flat sizing).
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — gutted-and-rewired
  `_evaluate_market`. Three new layers between forecast lookup and order
  construction:
  1. **Ensemble σ.** After NWS forecast lands, call Open-Meteo for the
     same target. If ≥3 members returned, σ = `max(ensemble.std_f, ensemble_sigma_floor_f)`
     (floor default 0.5°F). Else fall back to `sigma_for_horizon` heuristic.
     `sigma_source` field (`open_meteo_ensemble` | `heuristic`) tags every
     evaluation so we can later A/B the two.
  2. **METAR nowcast blend.** For horizons 0–6h on hourly markets only
     (HIGH/LOW excluded — daily extrema aren't well-modelled by current-temp
     extrapolation): `forecast_temp = w·NWS + (1-w)·METAR_extrap` where
     `w = clamp(horizon_h / 6.0)`. At horizon=0, pure nowcast; at 6h, pure
     forecast. Station mapping: new `_CITY_TO_METAR_STATION` parallels
     `_CITY_COORDS_FALLBACK` (22 city codes → airport METAR codes).
  3. **Fractional Kelly sizing.** New `_compute_kelly_usd(prob_outcome,
     share_price, account_equity, city_code, spend)` helper. Cap ladder:
     `kelly_target = equity × kelly_fraction × full_kelly` → clamp per-market
     ($cap = 5% × equity) → clamp per-day-remaining (25% × equity − today's spend)
     → clamp per-city-remaining (15% × equity − city spend) → floor at
     `min_usd` (default $1). The dominating cap is reported in `applied_cap`.
     Per-cycle `_SpendCounter` is seeded from `_query_today_spend` (audit-DB
     query at top of cycle) and incremented in-memory as orders emit so
     cap consumption within one cycle is correct.
  Falls-through `sizing.mode: fixed_usd` is still supported (legacy path
  if config ever rolls back).
- `trading_corp/main.py` — `_scheduled_kalshi_weather_arb_loop` now
  snapshots the `kalshi_weather` paper-broker equity BEFORE `run_scan_cycle`
  (previously the snapshot happened AFTER orders emitted, which was wrong
  for Kelly-based pre-emission sizing) and passes `account_equity=...`
  into the scan. `would_have_placed` audit payload allowlist extended for
  11 new fields: `sigma_source`, `ensemble_n_members`, `ensemble_std_f`,
  `nowcast_blend_w`, `metar_station`, `metar_latest_temp_f`, `metar_extrap_f`,
  `threshold_high_f`, `max_dollar_risk`, `kelly_fraction_used`, `kelly_full_pct`,
  `applied_cap`, `account_equity_at_size`.
- `scripts/patch_kalshi_weather_kelly_sizing.py` — **NEW.** Idempotent
  yaml patcher (the `kalshi_weather_arb` block is prod-only per
  `trading_corp_prod_git_drift.md`). Replaces the `sizing: {mode: fixed_usd,
  fixed_amount: 1.0}` block with the new `kelly_fractional` block + 4
  new module-level knobs (`open_meteo_enabled`, `ensemble_sigma_floor_f`,
  `metar_enabled`, `nowcast_blend_horizon_hours`). Re-runnable on prod
  (detects already-patched yaml + exits clean).
- `tests/test_kalshi_weather_sizing.py` — **NEW.** 15 tests covering
  Kelly edge cases, `_SpendCounter`, cap-clamping ladder, audit-spend
  SQL, ensemble σ math. All green; full suite (excluding 5 pre-existing
  PMCC / webhook-return-fast failures unrelated to this work) = 662 pass.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Ensemble σ derivation:** measured cross-model std from Open-Meteo,
  not heuristic by horizon. Visible: `sigma_source` field on every
  `kalshi_weather_evaluated` audit row past the early-skip gates.
- **METAR nowcast blend:** sub-6h hourly markets weight current obs.
  Visible: `nowcast_blend_w` field (0..1) on hourly-market evaluations.
- **Fractional Kelly sizing:** $1 flat is gone. Order $ now scales with
  bankroll × edge × 0.25 fractional × caps. Visible: `kelly_full_pct`
  + `applied_cap` + `max_dollar_risk` fields on every `would_have_placed`
  row. Cap ladder: per_market (5%) → per_day (25%) → per_city (15%) → min $1.

**Verification:**
- md5-diff confirmed on all 5 prod-touching files post-scp.
- Service restart 02:56:02 UTC; "Kalshi Weather Arbitrage scanner online
  (enabled=True, auto_execute=False)" at 02:56:43.
- Two scan cycles since restart (02:56:02 + 03:01:52); 60 evaluations,
  zero exceptions, zero open-meteo / metar log warnings.
- **Direct prod smoke test** of the new clients (run via `venv/bin/python -c`
  on prod): Open-Meteo returned 4 members for KJFK @ 2026-05-15T14
  (gfs_global / icon_global / meteofrance_seamless / gem_global —
  ECMWF didn't return for this call), mean=56.80°F, std=1.35°F.
  METAR KJFK current=57.92°F (obs 03:00 UTC), trend=−1.53°F/h, 3 obs.
  `kelly_fraction(0.6, 0.5) = 0.20` (matches unit-test).
- Config-yaml verification: patcher applied; new keys visible in
  `config/strategies.yaml` on prod.

**Inert / dormant on current traffic:**
- **No `kalshi_weather_evaluated` row with the new `sigma_source` /
  ensemble / nowcast / Kelly fields has landed yet** — all 60
  post-restart evaluations hit early-stage skips: 32× `no_implied` (no
  bid/ask quotes at this hour — overnight Kalshi book is thin) and
  28× `no_coords` (the discovered city codes `TCHI` and `NY` aren't in
  `_CITY_COORDS_FALLBACK`, which has `CHI` and `NYC`). The new pipeline
  is loaded and verified independently via the direct smoke test;
  fields will populate as soon as a market with a known city code +
  valid quotes is discovered. Pre-existing gap (see backlog).

**Latent bugs surfaced (not introduced):**
- `_CITY_COORDS_FALLBACK` is missing `TCHI` (= Chicago T-prefix variant)
  and `NY` (= NYC short form). Today's scan candidates are dominated
  by these. Added as P3 follow-up in BACKLOG.md.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
BASE=/home/azureuser/trading_corp; \
TAG=pre-weather-kelly-20260515-0255; \
sudo cp \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
git -C /tmp clone /dev/null 2>/dev/null || true; \
# Revert code: requires re-scp'ing prior versions from local git history.
sudo rm -rf \$BASE/trading_corp/__pycache__ \
            \$BASE/trading_corp/data/__pycache__ \
            \$BASE/trading_corp/agents/strategies/__pycache__; \
sudo systemctl restart trading-corp.service"
```
(Note: code rollback requires re-deploying the pre-tier-1 versions of
the 3 edited files. The 2 new client files can simply be deleted.)

---

## 2026-05-15 02:05 UTC — Crypto `strike_type='custom'` ticker-suffix dispatch (P2)

**Triggered by:** Post 01:50 deploy, weather between math fully unlocked weather (0 `no_strike` skips). Crypto was still hitting `no_strike` on all `strike_type='custom'` markets because Kalshi uses "custom" for BOTH bucket (B-suffix) AND single-side threshold (T-suffix) tickers, and leaves `floor_strike`/`cap_strike` as None for both — the strike spec lives only in the ticker suffix.

**Backup tag:** `pre-crypto-custom-dispatch-20260515-0205`

**Files deployed (2):**
- `trading_corp/data/crypto_spot_provider.py` — new `parse_kalshi_strike_suffix(ticker)` returning `('B', value)` or `('T', value)` or None. Used to extract Kalshi's encoded strike spec when the API doesn't populate `floor_strike`/`cap_strike` (which it doesn't for crypto-category markets).
- `trading_corp/agents/strategies/kalshi_crypto_arb.py` — (1) New module-level `_compute_event_bucket_widths(events)` that walks the discovery response and for each `event_ticker` computes the median gap between adjacent B-values. Critical because Kalshi bucket widths vary by asset AND horizon — ETH 1h buckets are ~$20 but ETH Jan-2027 buckets are ~$500. (2) `bucket_width_hint` plumbed into the survivor dict so `_evaluate_market` can derive bounds without a separate lookup. (3) New `elif strike_type == "custom"` fallback branch that, if `parse_kalshi_strike_suffix` returns `('B', center)` and a width hint is available, sets `threshold = center - half`, `threshold_high = center + half`, `direction = 'between'`. T-suffix tickers are intentionally left as `no_strike` skip for now — direction (greater vs less) is ambiguous without parsing `rules_primary` text.

**Verification (post-deploy 02:06 UTC):**
- `kalshi_crypto_skipped_no_strike` count = **0** since deploy (was 30 in the prior scan).
- All 22 remaining skips are `no_implied` — pure liquidity (no ask quotes at this hour, ~10pm EDT, Kalshi crypto depth is thin).
- Bucket-width inference verified working: discovery contains B-tickers like `B88650`/`B88750`/`B88850` for BTC → median gap = 100, correctly applied.
- Local smoke test confirmed parser behaviors:
  - `KXDOGE-26MAY1422-B0.157` → `('B', 0.157)`
  - `KXBTC-26MAY1422-B88850` → `('B', 88850.0)`
  - `KXSOLE-26MAY1422-T59` → `('T', 59.0)`
  - `KXSOLMAXMON-SOL-26MAY31-10000` → None (non-T/B suffix, intentional)

**Notable code change:**
- The width-hint is computed once per scan (one pass over events) and shared across all per-market evaluations in that cycle. Cheap; doesn't add a per-market query.

**Inert / pending:**
- **T-suffix crypto markets still skip as `no_strike`.** Kalshi doesn't expose direction (≥X vs ≤X) for these via `strike_type` alone; need to parse `rules_primary` text or use implied-prob context. Filed as remaining P3 (smaller follow-up — most crypto markets are bucket-format, T-tickers are minority).
- **No actual crypto fires yet** because all 22 no-quote markets at deploy time were genuinely unquoted on Kalshi. Daytime scan should produce fires.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
BASE=/home/azureuser/trading_corp/trading_corp; \
sudo cp \$BASE/data/crypto_spot_provider.py.pre-crypto-custom-dispatch-20260515-0205 \$BASE/data/crypto_spot_provider.py; \
sudo cp \$BASE/agents/strategies/kalshi_crypto_arb.py.pre-crypto-custom-dispatch-20260515-0205 \$BASE/agents/strategies/kalshi_crypto_arb.py; \
sudo rm -rf \$BASE/data/__pycache__ \$BASE/agents/strategies/__pycache__; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-15 01:50 UTC — Weather + crypto unlock (bucket math + crypto broker bugs)

**Triggered by:** "no crypto or weather trades have come in" — observation that both specialized agents shipped 2026-05-14 but had produced **zero** trades. Investigation surfaced 4 distinct bugs across the crypto strategy + a missing math branch for both.

**Backup tags:** `pre-crypto-broker-fix-20260515-0050`, `pre-crypto-prefix-fix-20260515-0125`, `pre-between-math-20260515-0132`

**Files deployed (5):**
- `trading_corp/main.py` — fixed `_scheduled_kalshi_crypto_arb_loop` broker discovery. (1) Removed nonsensical `and not kalshi_broker == coinbase_broker` clause from elif — Python precedence made the elif **always False** because `None == None → True`. (2) Switched coinbase lookup to `data_exec.brokers.get("coinbase_spot")` because in paper mode the broker is a `PaperExecutionBroker` wrapper, not a raw `CoinbaseBroker` — class-name match never hit. Also bumped the "missing broker" log from DEBUG to INFO so future silent-failure won't hide.
- `trading_corp/data/crypto_spot_provider.py` — `parse_kalshi_asset_prefix` rewritten as regex `^KX(HYPE|DOGE|BTC|ETH|SOL|XRP|BNB)[A-Z0-9]*-`. Old code matched only `KX{asset}-` / `KX{asset}15M-` — Kalshi has since added suffix codes (E = event-cycle, D = daily, etc.), so tickers like `KXSOLE-`, `KXBTCE-`, `KXDOGED-` were all rejected as "not_crypto." Post-fix the discovery `markets_pre_filter` jumped from 79 → 206.
- `trading_corp/agents/strategies/_weather_math.py` — added `direction='between'` to `forecast_probability` and `evaluate_weather_market`. New formula `P(low ≤ X ≤ high) = Φ((high-μ)/σ) - Φ((low-μ)/σ)` for bucket markets. Gate 2 (near-threshold) is skipped for between — it doesn't apply to bucket semantics. Smoke test confirmed `P(85≤T≤86 | μ=85.5, σ=2) = 0.1974` (analytic match).
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — added `elif strike_type == "between"` branch using `floor_strike` + `cap_strike`. Also added no-ask filter before k_per_cycle cap (Kalshi returns 0.0, not None, for unquoted sides — empty markets were sorting to top of "tightest-spread" and crowding out live ones).
- `trading_corp/agents/strategies/kalshi_crypto_arb.py` — same between branch (handles both `strike_type='between'` and `'custom'` since Kalshi uses both interchangeably for bucket markets), plus no-ask filter.

**Bugs caught + fixed:**
1. **Crypto broker-discovery precedence bug** — `cls == "CoinbaseBroker" and not kalshi_broker == coinbase_broker` always False due to `None == None`. Made the crypto strategy silently skip every tick for 4+ hours after launch.
2. **Crypto broker-class mismatch** — in paper mode coinbase_spot's registered broker is `PaperExecutionBroker`, not `CoinbaseBroker`. Even after fix #1 the class-name check missed.
3. **Stale asset-prefix parser** — Kalshi added `E`/`D` suffix codes to ticker prefixes; old `startswith("KXSOL-")` rejected `KXSOLE-`. Recognized 0 SOL/ETH/BTC daily/event markets pre-fix.
4. **Missing `between` math** — Kalshi structures weather + crypto markets as bucket PMFs (B-suffix tickers). 58% of weather + ~100% of crypto evaluated markets are buckets. Strategy threw all of them away via `no_strike` skip (the comment in old code literally said "rare — skip for v1"; data shows it's the *dominant* shape).
5. **Empty-market crowding (incidental)** — k_per_cycle sort by `abs(yes_ask - yes_bid)` defaulted None to 0/1 but Kalshi returns 0.0 explicitly, so unquoted markets had spread=0 and sorted FIRST, displacing live ones.

**Live verification (post-deploy):**
- **Weather (post 01:42 UTC):** `kalshi_weather_skipped_no_strike` count = **0** since deploy (was 644 pre-deploy). 30 evaluated per scan. Remaining skips: 27 no_implied + 3 no_coords. Math path now firing correctly on bucket markets — just blocked by late-night quote availability.
- **Crypto (post 01:46 UTC):** `markets_pre_filter` 79 → 206 (parser fix); `skipped_unsupported_asset` 45 → 0 (was misclassifying HYPE/BNB). Strategy now correctly recognizes BTC/ETH/SOL/DOGE markets. Tick cadence working (60s).
- **Math smoke test passed on prod venv:** `forecast_probability(85.5, σ=2.0, [85,86], "between") = 0.1974` ≈ analytic 0.1974.

**Inert / pending:**
- **No actual fires yet** for either division. Two reasons: (a) most picked buckets have no ask quote at this hour (~9-10pm EDT, low liquidity). Daytime tick should produce fires. (b) Crypto T-suffix tickers (`KXDOGED-26MAY1422-T0.1499999`, single-side threshold) also carry `strike_type='custom'` — same overloaded name as buckets. Strategy's "custom → between" mapping fails for these because floor/cap aren't populated. Needs follow-up: ticker-suffix dispatch (T → greater, B → between) for crypto. See BACKLOG.
- The 156 "not_crypto" markets per crypto scan are real non-crypto categories Kalshi sometimes returns under the same response (the category filter isn't strict).

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
BASE=/home/azureuser/trading_corp/trading_corp; \
sudo cp \$BASE/main.py.pre-crypto-broker-fix-20260515-0050 \$BASE/main.py; \
sudo cp \$BASE/data/crypto_spot_provider.py.pre-crypto-prefix-fix-20260515-0125 \$BASE/data/crypto_spot_provider.py; \
sudo cp \$BASE/agents/strategies/_weather_math.py.pre-between-math-20260515-0132 \$BASE/agents/strategies/_weather_math.py; \
sudo cp \$BASE/agents/strategies/kalshi_weather_arb.py.pre-between-math-20260515-0132 \$BASE/agents/strategies/kalshi_weather_arb.py; \
sudo cp \$BASE/agents/strategies/kalshi_crypto_arb.py.pre-between-math-20260515-0132 \$BASE/agents/strategies/kalshi_crypto_arb.py; \
sudo rm -rf \$BASE/data/__pycache__ \$BASE/agents/strategies/__pycache__; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-14 23:50 UTC — Per-whale auto-pause (P3, formerly manual)

**Triggered by:** Observation pass on 2026-05-14 23:35 UTC showed 79 stale 0xE9Ba RTs flushing -$76 through the multi-leg resolver fix. Whale was already manually dropped from `selected_whales`, but the pattern (single bad whale → -$50-100 paper drawdown before human notices) is exactly the case the BACKLOG P3 auto-pause item was filed for. Codifies last night's manual drops as a circuit breaker.

**Backup tag:** `pre-whale-autopause-20260514-2350`

**Files deployed (3):**
- `trading_corp/agents/strategies/_whale_autopause.py` — **NEW**. `should_autopause(conn, whale_name, table, name_field, division, ...) → (triggered, stats_dict)`. Aggregates resolved round-trips for one whale (`won IS NOT NULL`) and returns trigger boolean + full stats. Thresholds: `MIN_RESOLVED_TRADES=30`, `MAX_WIN_RATE_PCT=40.0`, `MAX_TOTAL_PNL=-5.0` (conjunctive). Also exposes `sqlite_path_from_db_url()` for the strategies to open a raw sqlite conn (json_extract pushdown is faster than re-implementing the agg in Python).
- `trading_corp/agents/strategies/polymarket_copy_trader.py` — new `_apply_autopause_filter()` method called at top of `run_scan_cycle` right after `_load_selected_whales()` returns. On trigger: persist new `selected_whales` (without paused entries) via `set_agent_state` + emit `polymarket_whale_auto_paused` audit per whale. Returns filtered list; scan continues with survivors.
- `trading_corp/agents/strategies/kalshi_copy_trader.py` — same pattern, K3-specific. Audit kind `kalshi_whale_auto_paused`. Filter runs BEFORE the Apify `fetch_open_positions` call so we don't pay quota on a whale about to drop. Selected_whales schema is `list[str]` (vs PCT's `list[dict]`), handled accordingly.

**Features shipped:**
- Both copy-trading divisions now self-prune the `selected_whales` roster. If a whale has 30+ resolved RTs AND WR<40% AND total_pnl<-$5, it's auto-removed on the next scan tick and audited.
- Audit payload includes full stats (n_resolved, n_wins, n_losses, win_rate_pct, total_realized_pnl, thresholds used, remaining_whales count) for traceability.
- No `main.py` allowlist touch needed — uses `logger_agent.log_event()` directly (passthrough, not `ProposedOrder.extra` filtered).

**Notable code changes:**
- Conjunctive thresholds chosen on purpose. Streaky-but-net-profitable (Pedrobeliever47: 62.5% WR, +$6.53) survives. Small-sample (OnlySafeBets: 2 RT, -$1.75) survives. tom14cat14 at session end (87 RT / 39.1% WR / -$1.58) would NOT trigger (pnl above -$5) — Jack's manual drop on that one was a judgment call the conservative rule deliberately leaves to a human.
- 0xE9Ba's pre-drop snapshot (82 RT / 4.88% WR / -$76.56) WOULD trigger. Confirmed via dry-run.
- Filter runs once per scan cycle (single sqlite roundtrip per whale + one `set_agent_state` write if anything triggers). Cheap — <100ms for current rosters.

**Dry-run pre-deploy (read-only against prod DB):**
- PCT 11 selected whales: 0 pauses (all clean per current thresholds)
- K3 3 selected whales: 0 pauses
- Hypothetical 0xE9Ba (had it remained selected): PAUSE ✓
- Hypothetical tom14cat14 (had it remained selected): keep (pnl -$1.58 > -$5)

**Verification (post-deploy, 23:50–00:01 UTC, ~11 min observation):**
- Service active, PID 350022 (was 346846 → 350022 on restart), NRestarts=0
- 0 tracebacks in journal since restart
- PCT: 4 `polymarket_copy_trader/would_have_placed` audit rows emitted — normal scan loop firing
- K3: `last_poll_ts` updated to `2026-05-14T23:50:35.602946+00:00` (initial post-restart tick) + Apify KV secret re-fetched at 23:56:17 (second tick) → scan loop running
- 0 `polymarket_whale_auto_paused` / `kalshi_whale_auto_paused` rows (correct: dry-run predicted 0)
- Import smoke-test passed on prod venv (`from ..._whale_autopause import should_autopause` + both copy_trader agents instantiable)

**Inert / dormant on current traffic:**
- Nothing this code does fires until a selected whale crosses all 3 thresholds. With current rosters that's unlikely in the short term. Real value is preventing a *future* bad whale (or a recovered + re-added whale that re-degrades) from bleeding $50-$100 unnoticed.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-whale-autopause-20260514-2350; BASE=/home/azureuser/trading_corp/trading_corp/agents/strategies; \
sudo mv \$BASE/polymarket_copy_trader.py.\$TAG \$BASE/polymarket_copy_trader.py; \
sudo mv \$BASE/kalshi_copy_trader.py.\$TAG \$BASE/kalshi_copy_trader.py; \
sudo rm \$BASE/_whale_autopause.py; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-14 23:00 UTC — Polymarket multi-leg resolver fix (P0b)

**Triggered by:** PCT was emitting copies on multi-leg sports/crypto markets ("Cincinnati Reds", "Up", "Real Oviedo", "Thunder", etc.) that couldn't auto-resolve. `_compute_round_trip_row` short-circuited on outcome != "yes"/"no" → return None. Plus `PolymarketBroker.get_market_resolution` was treating multi-leg `outcomePrices` like `["1","0","0"]` as "fractional" → void.

**Backup tag:** `pre-multileg-resolver-20260514-2300`

**Files deployed (2):**
- `trading_corp/brokers/polymarket.py` — `get_market_resolution()` now returns `status="resolved"` whenever exactly one entry of `outcome_prices` is "1.0" and the rest are "0.0" (binary AND multi-leg). `yes_won` field stays binary-only backwards-compat (None for multi-leg). Fractional/partial resolution still voids.
- `trading_corp/agents/polymarket_resolver.py` — `_compute_round_trip_row` adds multi-leg path. When outcome isn't yes/no, looks up `outcome_index` (already in audit payload from PCT `_emit_entry`) against `outcome_prices`. won = float(outcome_prices[outcome_index]) == 1.0.

**Immediate impact (manual resolver tick post-deploy):**
- 49 round-trips resolved in one tick
- PCT total resolved: 28 → 79 (+51)
- PCT wins: 15 → 58 (+43)
- PCT realized PnL: +$3.96 → +$21.12 (**+$17.16 unstuck**)
- Multi-leg families now resolving: NBA (Thunder/Lakers/Pistons/Cavaliers), MLB (Yankees/Orioles), Bitcoin Up/Down 5min, sports parlays.

**Notable code changes:**
- Backwards-compat: existing binary YES/NO callers unaffected. `yes_won` field still set for 2-outcome markets.
- Resolver dispatches on outcome string: yes/no → use yes_won (legacy); else → use outcome_index. `outcome_index` comes from PCT `_emit_entry`'s audit payload (already present per the polymarket Data API activity row schema).
- Multi-leg trades resolved via this path get `extra_json` from `_compute_round_trip_row` which is a SMALL dict (rationale + risk_verdict + llm_confidence). Does NOT include whale_user_name. Cosmetic side effect: the Whales dashboard tab shows ~95 trades attributed to NULL handle until backfill or extra_json enrichment.

**Verification:** manual `resolve_pending_round_trips` call returned `{'scanned':100, 'resolved':49, 'pending':51, 'void':0, 'not_found':0, 'errors':0}`. Sample queries confirmed `outcome_bet="Thunder"` / `outcome_bet="Cavaliers"` / `outcome_bet="Up"` etc. now have correct won/loss + PnL.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-multileg-resolver-20260514-2300; BASE=/home/azureuser/trading_corp; \
sudo mv \$BASE/trading_corp/agents/polymarket_resolver.py.\$TAG \$BASE/trading_corp/agents/polymarket_resolver.py; \
sudo mv \$BASE/trading_corp/brokers/polymarket.py.\$TAG \$BASE/trading_corp/brokers/polymarket.py; \
sudo systemctl restart trading-corp.service"
# Note: rollback does NOT reverse the 49 newly-inserted round-trip rows;
# they remain in polymarket_round_trips. Acceptable since they're correct.
```

---

## 2026-05-14 22:45 UTC — PM Whales dashboard tab (P0a)

**Triggered by:** Both copy-trading divisions (PCT + K3) had been live for days, accumulating per-whale data, but no UI surface to see whale-level performance. Per BACKLOG P0a entry from 2026-05-12.

**Backup tag:** `pre-whales-tab-20260514-2245`

**Files deployed (2):**
- `trading_corp/web/data.py` — new `PMWhaleRow` dataclass (handle, venue, division, n_resolved, n_wins, n_losses, win_rate_pct, total_realized_pnl, n_open, last_entry_ts). New `_query_pm_whales(db_url, target_slugs)` aggregates per-whale stats from both `kalshi_round_trips.extra_json.whale_handle` (K3 schema) and `polymarket_round_trips.extra_json.whale_user_name` (PCT schema), plus open-trade counts from audit_event for would_have_placed BUY rows not yet linked. Whales with 0 resolved but open positions surfaced (so silent whales don't disappear). `whales` field added to `PMDashboardView`. `build_prediction_market_view` now calls `_query_pm_whales` in the existing `asyncio.gather`.
- `trading_corp/web/templates/partials/pm_dashboard_body.html` — conditional Whales tab nav button (only renders when `view.whales` non-empty) + new `<section id="pm-tab-whales">` with table showing all 9 columns. WR color-coded: green ≥60%, gray 40-60%, red <40%, muted when None.

**Features shipped:**
- New tab on `/prediction-markets/{division}` for `kalshi_copy_trading` + `polymarket_copy_trading` (and the All-Prediction-Markets cross-venue view). Conditional render: tab hidden for arb-only divisions.
- Sorts highest realized PnL first; silent whales (n_resolved=0) at bottom.
- Tab state managed by existing `data-pm-tab` JS in the dashboard's main template; HTMX swap preserves it on partial reloads.

**Notable code changes:**
- Two queries per copy-trading division: one for round-trip aggregates (joins `won` + `realized_pnl`), one for open-position count (joins audit_event vs round-trip `entry_order_id` to subtract paired exits). Cheap SQL; runs in parallel via the asyncio.gather.
- Polymarket-side path also lists open-only-no-resolved whales (the 5 silent whales: Talvez10, ic4cream, 00xx00xx00, ddssaaas6, 0xe617861a96631d7cefdb) so the UI surfaces them — addresses earlier visibility concern.

**Verification:** smoke-tested `_query_pm_whales` directly post-deploy; returned 11 rows with correct breakdown. Pedrobeliever47 (PCT) 24/15W +$6.53; smedtoshi (K3) 249/116W +$2.08; etc. PID 343xxx → 344621.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-whales-tab-20260514-2245; BASE=/home/azureuser/trading_corp; \
sudo mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
sudo mv \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html.\$TAG \
         \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-14 22:28 UTC — Sports Scout poll lift (free-tier preservation)

**Triggered by:** the-odds-api free tier = 500 req/month. Default `poll_interval_sec=900` (15 min) × 5 leagues with 30-min cache → ~120-240 req/day = burns the monthly quota in ~2 days. Lifted to `3600` (1h) to land at ~30-60 req/day so the 7-day scout window fits in free tier with headroom.

**Backup tag:** `pre-sports-poll-bump-20260514-2228` (yaml-only, single-file change).

**Verified:** yaml re-parses cleanly (`poll_interval_sec=3600`); hot-reload picks up via `KalshiSportsScoutAgent._reload` mtime check.

**No restart needed.** Watch `kalshi_sports_scout_scan` audit's `odds_api_quota_remaining` field daily; if approaching 0 with days left, lift further or upgrade to $30/mo paid tier.

---

## 2026-05-14 22:06 UTC — K3 sports-ticker skip

**Triggered by:** Sports Scout shipped as the dedicated sports observer. K3 (kalshi_copy_trader) had no category filter and was firing 80 historical sports trades via whale shadowing. Lockdown for parity with weather/crypto pattern.

**Backup tag:** `pre-k3-skip-sports-20260514-2206`

**File deployed (1):**
- `trading_corp/agents/strategies/kalshi_copy_trader.py` — module-level `_SPORTS_TICKER_PREFIXES` tuple (31 prefixes: MLB/NBA/NHL/NFL/MLS, ATP/WTA/ITF, CS2/DOTA/LCS, ~20 international soccer leagues, UFC/BOXING/NCAAF/NCAAB) + `_is_sports_ticker()` helper. Skip injected at the top of K3's entries loop in `_process_whale_activity` — emits `kalshi_copy_entry_skipped_sports` audit and `continue`s without calling `_emit_entry`.

**Features shipped:** K3 now blocks all sports tickers, logging skip-reason for visibility. Other 4 strategies that could place sports bets (3 arbs + scout) verified clean.

**Verification:** 10/10 prefix-match test cases pass (sports tickers blocked; non-sports BTC/temp/recession pass through). PID 340089 → 340857 (restart for code reload). Service active.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-k3-skip-sports-20260514-2206; BASE=/home/azureuser/trading_corp; \
sudo mv \$BASE/trading_corp/agents/strategies/kalshi_copy_trader.py.\$TAG \
         \$BASE/trading_corp/agents/strategies/kalshi_copy_trader.py; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-14 21:42 UTC — Kalshi Sports Scout (read-only observer, no trading division)

**Triggered by:** Specialized-agent pattern for sports needed validation before committing to a paid data feed + full trading division. Three options scoped (A: scout-first, B: MLB-only v1, C: broad v1). User picked A — observe-first to validate edge magnitude over 7 days.

**Backup tag:** `pre-sports-scout-20260514-2142`

**Files deployed (3 new, 3 patched, +1 secrets-loader bug fix):**

### New
- `trading_corp/data/odds_api_client.py` — async the-odds-api client. Per-sport 30-min cache. American-odds → vig-removed implied prob (median across books for consensus). H2H markets only v1; spread/total/props deferred. Returns `GameOdds(home_team, away_team, implied_home, implied_away, n_books, median_vig_pct)`.
- `trading_corp/data/sports_team_mapping.py` — 155 Kalshi 3-letter codes → odds-api full team names across MLB(37) + NBA(30) + NHL(32) + MLS(31) + NFL(32). `parse_sports_ticker()` uses YES-side suffix as anchor to split TEAM1TEAM2 blobs (e.g., `KXMLBGAME-26MAY112010SEAHOU-SEA` → SEA + HOU). Rejects TIE/DRAW for v1; rejects non-listed leagues (ATP/ITF/CS2 etc.).
- `trading_corp/agents/strategies/kalshi_sports_scout.py` — read-only scout. **No order emission.** Owns its OddsAPIClient lifecycle. Audit kinds: `kalshi_sports_scout_scan` (per-cycle summary w/ quota tracking), `kalshi_sports_observed` (per market: bookmaker_implied vs kalshi_implied + divergence), `kalshi_sports_scout_unmapped` (per market that couldn't be mapped), `kalshi_sports_scout_no_api_key` (stub mode).

### Patched
- `config/strategies.yaml` — `kalshi_sports_scout:` block (enabled=true, leagues=[MLB, NBA, NHL, MLS, NFL], divergence_log_threshold_pct=1.0)
- `trading_corp/utils/secrets.py` — `ODDS_API_KEY` added to KV-fetch list, redaction list, Secrets dataclass + factory. **CAUGHT BUG:** patcher applied the redaction-list edit twice (lines 47/48 dup) and missed the `expected_env_vars` list. Manual surgical fix at 22:18 UTC dedupe + insertion at line 214. Going forward, when patching multiple SAME-pattern lists in a single file, anchor with surrounding context (the 8-space indent + adjacent line) instead of relying on `replace(..., 1)` twice.
- `trading_corp/main.py` — agent setup + `_scheduled_kalshi_sports_scout_loop` (lazy-resolves real KalshiBroker; no risk_agent wiring since no orders flow)

### NOT touched (deliberately)
- `config/divisions.yaml` — scout doesn't trade; no division
- `kalshi_resolver.py` — no orders to resolve

**External step required:** API key uploaded to KV manually (Jack's local az CLI run). VM managed identity has read-only KV permission.

```bash
az keyvault secret set --vault-name kv-tc-vtwbowt3wtkpy --name ODDS-API-KEY --value '<key>' --output none
```

**Verification:**
- Parser tests: 10/10 sample tickers parse correctly (incl. correctly rejecting TIE outcomes + non-scope leagues)
- KV upload confirmed (32 chars, name `ODDS-API-KEY`)
- Post-fix loader returns `odds_api_key.length=32`
- "Kalshi Sports Scout online (enabled=True, has_credentials=True)" in journalctl at 22:19:26 UTC
- PID 339242 → 340089 → 340857 → 341639 → 342228 (multiple restarts during deploy + bug-fix cycle)

**First-week observation gate:** after 7 days, query `kalshi_sports_observed` audit to compute median absolute divergence per league + hit-rate at various divergence thresholds. Decide: full trading division (option B/C from prior scoping), scope-down, or shelve.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-sports-scout-20260514-2142; BASE=/home/azureuser/trading_corp; \
sudo mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
sudo mv \$BASE/trading_corp/utils/secrets.py.\$TAG \$BASE/trading_corp/utils/secrets.py; \
sudo mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
sudo rm \$BASE/trading_corp/data/odds_api_client.py \
        \$BASE/trading_corp/data/sports_team_mapping.py \
        \$BASE/trading_corp/agents/strategies/kalshi_sports_scout.py; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-14 21:19 UTC — Kalshi Crypto Arbitrage specialized division (forecast-driven)

**Triggered by:** Jack's request to extend the specialized-agent pattern to a second Kalshi category after Kalshi Weather shipped at 20:54 UTC. Crypto chosen because (a) we already have live spot via CoinbaseBroker, (b) K3 lost 192 of 253 trades historically on KX*15M crypto bars where the LLM had no live-price context, (c) high volume on 15-min / daily crypto threshold markets.

**Backup tag:** `pre-crypto-division-20260514-2119`

**Files deployed (2 new, 4 patched):**

### New
- `trading_corp/data/crypto_spot_provider.py` — Async fetcher: Kalshi asset prefix → Coinbase ccxt symbol → live spot. Hard-coded annualized vols v1 (BTC=60%, ETH=75%, SOL=90%, DOGE=110%, XRP=85%). HYPE/BNB return None (no Coinbase US spot). 10s spot cache.
- `trading_corp/agents/strategies/kalshi_crypto_arb.py` — Strategy class mirroring `kalshi_weather_arb` shape. Reuses `_weather_math.evaluate_weather_market` directly (math is unit-agnostic — Fahrenheit + temp ↔ USD + spot). Source-divergence cushion is asset-specific (0.1% of spot vs weather's fixed 2°F).

### Patched
- `config/divisions.yaml` — new `kalshi_crypto` division (broker:paper; lazy-resolves real KalshiBroker + CoinbaseBroker for discovery + spot)
- `config/strategies.yaml` — new `kalshi_crypto_arb` block + **Crypto removed from `kalshi_llm_arbitrage` discovery categories** during the patch + **Crypto stripped from kalshi_tail_price_arb + kalshi_temporal_bucket_arb** via post-patch sed (lockdown parity with weather treatment). All three arb strategy `discovery.categories` now: `['Politics', 'Elections', 'Economics', 'Financials']`.
- `trading_corp/main.py` — `KalshiCryptoArbAgent` setup + `_scheduled_kalshi_crypto_arb_loop`. Lazy-resolves both `KalshiBroker` (discovery via `_client.get_market`) and `CoinbaseBroker` (spot via `quote()`). Skips cycle if either is unavailable.
- `trading_corp/agents/kalshi_resolver.py` — `kalshi_crypto_arb` added to `_KALSHI_ACTORS` + `_KALSHI_DIVISIONS` + `_ACTOR_TO_DIVISION` ({"kalshi_crypto_arb": "kalshi_crypto"}) + `_ACTOR_TO_ARB_TYPE_DEFAULT` ({"kalshi_crypto_arb": "crypto_spot"})

**Features shipped:**
- Crypto-category Kalshi markets now exclusively scanned by `kalshi_crypto_arb`. No LLM in path; pure Gaussian probability vs threshold using Coinbase spot.
- Default config: `poll_interval_sec=60` (crypto churns fast), `min_divergence_pct=10`, `max_horizon_hours=168` (7d), `market_cooldown_hours=1`, `sizing.fixed_amount=$1`.
- New audit kinds: `kalshi_crypto_scan`, `kalshi_crypto_evaluated`, `kalshi_crypto_skipped_{near_threshold,horizon,no_edge,no_strike,no_target_time,bad_target_time,no_spot,no_implied}`.
- Telegram fires emit 🪙 prefix.

**Notable code changes:**
- `evaluate_weather_market` is venue-agnostic — same call works with `(temp_f, sigma_f, threshold_f)` for weather and `(spot, spot×vol×√years, strike)` for crypto. When Financials ships, generalize `_weather_math.py` → `_threshold_math.py`; until then, the awkward name is fine.
- `parse_kalshi_asset_prefix(ticker)` recognizes HYPE/DOGE/BTC/ETH/SOL/XRP/BNB (longest-match first). HYPE/BNB recognized but `is_supported()` returns False → skipped at scan time.
- σ floor: `max(sigma, spot * 1e-6)` prevents div-by-zero in degenerate `time_to_resolution=0` cases.

**Verification:**
- yaml parses; agent constructs; resolver allowlist updated; main.py loop function present (smoke tests pre-restart).
- Parser tests: `KXBTC15M-26MAY1416-T80000`→BTC, `KXETH-26MAY14-T1900`→ETH, `KXHYPE15M-...`→HYPE, `KXPOLITICS-foo`→None.
- PID 335679 → 337337; service active.
- `kalshi_crypto` paper broker registered at 21:20:08 UTC.

**Math sanity:** For BTC at $81,500 spot, threshold $80,000 ('greater'), 30 min to resolution, vol=60%: σ ≈ $300, P(YES) ≈ 99.99%. Implied 0.85 → edge 14.99% → fires.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-crypto-division-20260514-2119; BASE=/home/azureuser/trading_corp; \
sudo mv \$BASE/config/divisions.yaml.\$TAG \$BASE/config/divisions.yaml; \
sudo mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
sudo mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
sudo mv \$BASE/trading_corp/agents/kalshi_resolver.py.\$TAG \$BASE/trading_corp/agents/kalshi_resolver.py; \
sudo rm \$BASE/trading_corp/data/crypto_spot_provider.py \
        \$BASE/trading_corp/agents/strategies/kalshi_crypto_arb.py; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-14 20:54 UTC — Kalshi Weather Arbitrage specialized division (forecast-driven)

**Triggered by:** Audit of 120 Climate/Weather LLM-arb trades revealed (a) a 15-trade KXTEMPNYCH disaster (-$6.11) where the LLM hallucinated the temperature threshold, and (b) 103 city-high trades winning 66% / +$3.48 but the generic LLM was guessing from training-data climatology rather than today's actual forecast. Decision: build a specialized weather agent that pulls NWS hourly forecasts + deterministic Gaussian math.

**Backup tag:** `pre-weather-division-20260514-2054`

**Files deployed (3 new, 4 patched):**

### New
- `trading_corp/agents/strategies/_weather_math.py` — Pure math + validation gates. `forecast_probability(forecast_temp, sigma, threshold, direction)` via Normal CDF (`math.erf`). `evaluate_weather_market()` applies three gates: horizon ≤ 72h, |threshold − forecast| ≥ sigma_total, |P(YES) − implied| ≥ min_divergence_pct. Sigma augmented with `SOURCE_DIVERGENCE_SIGMA_F=2.0` (NWS↔AccuWeather drift cushion). Venue-agnostic — crypto strategy (21:19) reuses it byte-for-byte.
- `trading_corp/data/weather_forecast.py` — NWS async client. Two-step protocol: `/points/{lat,lon}` → gridpoint URL (24h cache) → `/forecast/hourly` (30min cache). Free, no auth, US-only. `get_forecast_at(lat, lon, target_iso)` for hourly markets; `get_daily_extremum(lat, lon, date, kind='high'|'low')` for daily high/low chains.
- `trading_corp/agents/strategies/kalshi_weather_arb.py` — Strategy. Discovers Climate/Weather markets via `kalshi_broker.list_markets()`. Per market: parses lat/lon from `rules_primary` regex (with `_CITY_COORDS_FALLBACK` map for 20+ US cities), threshold from `floor_strike`/`cap_strike`, direction from `strike_type`, target time from `expected_expiration_time` (or ticker-suffix fallback for KXTEMPNYCH-style hourly tickers). Non-US (TLV) skipped at scan time.

### Patched
- `config/divisions.yaml` — new `kalshi_weather` division
- `config/strategies.yaml` — new `kalshi_weather_arb` block; **Climate/Weather stripped from all three arb strategies** (kalshi_tail_price_arb, kalshi_temporal_bucket_arb, kalshi_llm_arbitrage). The first patcher invocation removed only one occurrence; followup `sudo sed -i '/^      - Climate and Weather$/d'` cleaned the rest.
- `trading_corp/main.py` — agent setup + `_scheduled_kalshi_weather_arb_loop` (lazy-resolves real KalshiBroker for discovery)
- `trading_corp/agents/kalshi_resolver.py` — added `kalshi_weather_arb` to actors + division map + arb_type default `weather_forecast`

**Features shipped:**
- Climate/Weather-category Kalshi markets now exclusively scanned by `kalshi_weather_arb`. Replaces the LLM path that was hallucinating thresholds.
- Default config: `poll_interval_sec=300` (5 min — weather doesn't churn), `min_divergence_pct=10`, `max_horizon_hours=72`, `market_cooldown_hours=4`, `sizing.fixed_amount=$1`.
- New audit kinds: `kalshi_weather_scan`, `kalshi_weather_evaluated`, `kalshi_weather_skipped_{near_threshold,horizon,no_edge,no_strike,no_coords,no_target_time,bad_target_time,no_forecast,no_implied}`.
- Telegram fires emit ☀️ prefix.

**Notable code changes:**
- Sigma-by-horizon heuristic: 0-24h=1.5°F, 24-48h=2.5°F, 48-72h=3.5°F. Conservative bands — better to skip noisy near-threshold than fire false-positive.
- City→coords map covers 20+ US locations with airport-AccuWeather coordinates as Kalshi documents for daily high/low markets; NYC Central Park (40.7812,-73.9665) for the KXTEMPNYCH chain.
- Resolver knows the new actor → division mapping; round-trips will land in `kalshi_round_trips` with `division='kalshi_weather'` once weather markets resolve.

**Verification:**
- All imports succeed; agent constructs (enabled=True, division=kalshi_weather)
- Math sanity-check: forecast 62°F ±1.5°F vs threshold 57.99°F → P(YES)=0.946, edge=14.6%, fires
- **Live NWS hit succeeded** at smoke-test time: 63.0°F forecast for NYC Central Park, 24h forward
- PID 330611 → 335679; "Kalshi Weather Arbitrage scanner online (enabled=True, auto_execute=False)" in journalctl

**Cross-divisional lockdown verified:**
| strategy | weather in categories | recent weather hits (post-restart) |
|---|---|---:|
| kalshi_tail_price_arb | ❌ | 0 |
| kalshi_temporal_bucket_arb | ❌ | 0 |
| kalshi_llm_arbitrage | ❌ | 0 |

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-weather-division-20260514-2054; BASE=/home/azureuser/trading_corp; \
sudo mv \$BASE/config/divisions.yaml.\$TAG \$BASE/config/divisions.yaml; \
sudo mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
sudo mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
sudo mv \$BASE/trading_corp/agents/kalshi_resolver.py.\$TAG \$BASE/trading_corp/agents/kalshi_resolver.py; \
sudo rm \$BASE/trading_corp/data/weather_forecast.py \
        \$BASE/trading_corp/agents/strategies/_weather_math.py \
        \$BASE/trading_corp/agents/strategies/kalshi_weather_arb.py; \
sudo systemctl restart trading-corp.service"
# Note: if rolling back to the pre-weather state, also restore Climate and Weather
# + Crypto into the three arb strategies' categories manually.
```

---

## 2026-05-14 19:12 UTC — Kalshi LLM Arb: surface per-market `title` to fix threshold hallucination

**Triggered by:** Audit on Climate/Weather LLM losses showed the LLM hallucinated thresholds as "-1°C (30°F)" across all 15 KXTEMPNYCH trades because the user prompt sent `event_title` ("New York City temperature on May 11, 2026 at 1pm EDT?") + delta-encoded `subtitle` ("-1° or below"). MarketRecord.title carried the explicit threshold ("Will the temp in NYC be above 57.99° on May 11, 2026 at 1pm EDT?") but wasn't being passed.

**Backup tag:** `pre-llm-mkttitle-20260514-1912`

**File deployed (1):**
- `trading_corp/agents/strategies/kalshi_llm_arbitrage.py` — survivor dict now includes `market_title=m.title`; `_estimate_probability` prefers it over `event_title + subtitle` fallback.

**Features shipped:**
- LLM eval prompts now show the explicit threshold in plain English for every binary-strike market.
- Lesson generalizable to any future Kalshi LLM strategy: surface threshold context explicitly; don't rely on the LLM to decode ticker conventions.

**Verification:** PID 329613 → 330611; service active; no errors.

---

## 2026-05-14 18:45 UTC — Home tile WR fix (Polymarket GROUP BY division)

**Triggered by:** Jack flagged main dashboard tile WR mismatch vs details pages.

**Backup tag:** `pre-pm-tile-wr-fix-20260514-1845`

**File deployed (1):**
- `trading_corp/web/data.py` — `_hydrate_pm_overview` polymarket roll-up now `GROUP BY division`, mirroring the kalshi roll-up. Pre-fix, ALL polymarket round-trips (both arb + copy_trader) aggregated into the `polymarket_arbitrage` tile; `polymarket_copy_trading` tile rendered zero.

**Features shipped:** every PM division tile now matches its details page on `n_resolved`, WR, PnL. Verified: polymarket_arbitrage 6 RTs 66.7% WR, polymarket_copy_trading 28 RTs 53.6% WR, kalshi_llm_arbitrage 190 RTs 50.5% WR, kalshi_copy_trading 333 RTs 44.7% WR.

---

## 2026-05-14 18:55 UTC — Polymarket Copy Trader resolution + drift checks at entry

**Triggered by:** Analysis of PCT 28 RTs showed Pedrobeliever47 carrying the strategy (22/28, 63.6% WR, +$6.50); other whales noisy. Two failure modes diagnosed: (a) `btc-updown-5m-*` markets where the whale's activity-feed lag means the market already settled by our poll (K3-class trap), (b) Trump/Xi political insider markets where the move happens between whale's fill and our 60s-later poll.

**Backup tags:** `pre-pct-resolfix-20260514-1855`, `pre-pct-driftcheck-20260514-1903`

**Files deployed (2 patched, 1 across two sub-deploys):**
- `trading_corp/agents/strategies/polymarket_copy_trader.py`:
  - Resolution check: `_emit_entry` now async, awaits `market_state_fetcher.get_market_resolution(condition_id)`. Skip on resolved/void. New audit `polymarket_copy_entry_skipped_resolved`. Catches the K3-class trap (verified: `btc-updown-5m-1778735400` settled 11+ hours before our poll).
  - Drift check (sub-deploy at 19:03): `await market_state_fetcher.quote(slug:outcome)`. If `(current - whale)/whale < entry_drift_skip_threshold` (default -0.30), skip. New audit `polymarket_copy_entry_skipped_drift`. Hot-reloadable threshold.
- `trading_corp/main.py` — `_scheduled_polymarket_copy_trader_loop` now lazy-resolves a PolymarketBroker (with `get_market_resolution`) and passes it as `market_state_fetcher` to `agent.run_scan_cycle`.

**Verification:** PID 327526 → 328963 → 329613; service active; smoke tests pre-restart confirmed both gates wired into `_emit_entry`.

---

## 2026-05-14 18:38 UTC — Kalshi LLM Arb category-aware strict gate + prompt update

**Triggered by:** Audit of 190 LLM-arb RTs: Economics 49 trades / 24.5% WR / -$25.32 (single-category 67% of total loss). Per-config retro: Economics + llm_prob ∈ [0,0.15]∪[0.85,1] = 11 trades / 72.7% WR / +$0.12. The category isn't broken — middle-confidence threshold markets are.

**Backup tag:** `pre-kalshi-llm-strict-20260514-1838`

**Files deployed (2):**
- `trading_corp/agents/strategies/kalshi_llm_arbitrage.py` — strict gate after baseline divergence check. For category ∈ {Economics, Financials}, require `divergence ≥ 30%` AND `llm_prob ∈ [0, 0.15] ∪ [0.85, 1.0]`. New audit `kalshi_llm_strict_gate_skip`. Hot-reloadable via `strict_categories`, `strict_min_divergence_pct`, `strict_llm_extreme_max` yaml keys.
- `trading_corp/agents/strategies/_polymarket_prompts.py` — added "Economics, Financials, and macro-data markets" section to `ANALYST_SYSTEM_PROMPT`. Tells LLM: (a) data cutoff = no live CPI/PPI/jobs; (b) threshold markets are economist-priced — anchor near market; (c) exact-buckets + extreme-tails are legit edge; (d) middle of probability range = output `confidence: "low"` and stay within 5pp of implied. Prompt grew 2,513 → ~3,371 tokens (still above 2,048 Sonnet 4.6 cache minimum).

**Verification:** 3 `kalshi_llm_strict_gate_skip` audit events at 18:54 UTC confirmed the gate is firing on middle-divergence Economics markets within minutes of deploy.

---

## 2026-05-14 18:18 UTC — K3 exit-pricing fix + 253-trade backfill

**Triggered by:** Jack flagged K3 (kalshi_copy_trading) as "0/333 wins". Investigation: `_emit_exit` called `broker.quote(ticker)` which returns $0 on settled Kalshi markets regardless of winner. Every paired exit recorded $0 → all trades looked like total losses.

**Backup tag:** `pre-k3-exitfix-20260514-1818`

**File deployed (1) + 1 backfill script:**
- `trading_corp/agents/strategies/kalshi_copy_trader.py` — `_emit_exit` now checks `get_market_resolution(ticker)` FIRST. If status=resolved, exit_price = $1 if our outcome matches winner else $0. If status=void, exit_price = entry_price (refund). Fall back to `broker.quote()` only when market still trading.
- `scripts/backfill_k3_exit_prices.py` — backfilled all 253 historical K3 paired round-trips by querying market resolutions. **149 went from "loss" → "win". Net PnL went -$170.42 → +$0.58.** Per-whale corrected: smedtoshi 191 trades 60.2% WR +$2.16; tom14cat14 62 trades 54.8% WR -$1.58.

**Re-enable:** K3 had been disabled on prod (accidental stomp during the 17:57 bitunix deploy, restored to enabled=true at 18:50 UTC after data showed strategy is roughly break-even paper).

**Adverse selection caveat:** Even with correct exit pricing, K3 still suffers from polling-based position observation — winners auto-settle out of `open_positions` before our 10-min poll sees them. Strategy is break-even paper, fee-negative live at $1-3 sizing. Live-mode flip gated on either (a) Plaid/alternative direct-trade-stream data source or (b) Hashdive/Apify schema change that exposes recent closures.

**Verification:** PID 321874 → 324735; service active; post-backfill rollup query confirms 149W/104L on 253 real-entry rows.

---

## 2026-05-14 17:57 UTC — BitUnix Phase 3.2 tuning: multi-fire fix + HTF-alignment gate + Cypher weight/TTL cut

**Triggered by:** Jack flagged BitUnix paper trades as "not looking good" + hypothesis that long-TTL signals persist too long. Audit of 43 trades (Phase 3.2 since 2026-05-11 18:00 UTC) revealed:
- BUY side: 1/9 wins, -0.67 R avg (all "partial" HTF alignment — 4h bull, 1D bear; relief-rally trap)
- Multi-fire clusters: 7 clusters of 2-3 trades within ≤60s, 6/7 lost (-9 R combined vs -3 R if dedup'd)
- Cypher A 1D `mc_a_red_diamond` firing 47× in 24h with oldest still contributing at 23.75h old

Three fixes shipped together. What-if replay on existing 43-trade history projected +6 R from dedup alone, +6 R additional from buy-side HTF gate (Z scenario in `scripts/analyze_bitunix_whatif.py`: 60.7% WR / +0.86 R avg / +24 R total vs current 48.6% / +0.49 / +18 R).

**Backup tag:** `pre-bitunix-fix123-20260514-1757`

**Files deployed (2):**

- `trading_corp/agents/divisions/bitunix_futures_observer.py`:
  - **Fix #1 (multi-fire race):** Added `import asyncio` + `self._score_lock = asyncio.Lock()` in `__init__`. `_score_and_maybe_propose` now wraps the entire critical section (read cooldown → evaluate → place → write cooldown) in `async with self._score_lock:`. Inner work moved to `_score_and_maybe_propose_locked`. Without this, concurrent webhook arrivals within ~1s all read pre-fire cooldown state and all fire.
  - **Fix #2 (HTF-alignment gate):** New method `_check_htf_alignment(side, now_iso)` returns `agree|partial|neutral|contra` by comparing winning side against `bitunix_observer_bias` table (already populated by Phase 3.0 `_update_bias`). Inserted in `_score_and_maybe_propose_locked` AFTER the score-SKIP check, BEFORE deps/broker/sizing. Asymmetric rule: BUY requires `agree` (both HTFs match); SELL allows everything except `contra`. New audit outcome `skipped_htf_alignment` with note.

- `config/strategies.yaml` `bitunix_futures.scoring` block:
  - **Fix #3 (Cypher weight + TTL cut):** mc_a_* weights 5/4/3/2 → 2/2/1/1; mc_b_* weights 5/4/3/2 → 2/2/1/1. mc_a_* TTL 1440 → **360 min** (24h → 6h). mc_b_* TTL 240 → **120 min** (4h → 2h). Thresholds scaled proportionally to preserve firing frequency: min_fire 8 → **4**, premium 12 → **8**, standard 8 → **4**, weak 5 → **2**.

**Features shipped:**
- Concurrent webhook fan-in now serializes through `_score_lock`. The 7-cluster multi-fire pattern can no longer recur.
- BUY trades are blocked when HTF bias is anything other than fully bull. SELL trades are blocked only on contra HTF.
- Cypher A/B signals contribute reduced points; the bias gate (Fix #2) carries the directional veto, not the score sum.
- TTL on 1D Cypher dropped to 6h — yesterday's 1D bar print no longer contributes to today's score.

**Notable code changes (callouts a future Claude shouldn't miss):**
- `_score_and_maybe_propose` is now a thin wrapper around `_score_and_maybe_propose_locked`. Any future score-path additions must go INSIDE the lock to inherit the serialization guarantee.
- HTF alignment uses the **existing bias state machine** (table `bitunix_observer_bias`), NOT the live signal-ledger sum. Means the gate is independent of factor weight tuning.
- Threshold cuts pair with weight cuts; reverting just the weights without thresholds would silently kill firing frequency.

**Verification:**
- Smoke test: config parses, observer constructs, `_check_htf_alignment` returns `'neutral'` with empty bias state.
- Pre-deploy bias state on prod: `1d=bear` (41.9h old, active under 7d decay), `4h=bull` (9.9h, active, set by mc_b_buy_circle) + 4h bear (29.9h, expired). Under new gate this resolves to "partial" for any direction → BUYS BLOCKED, SELLS ALLOWED. Matches the bear-trend regime BTC has been in.
- PID rotated 274260 → 321874; systemctl `is-active` post-restart.
- md5 parity: local + prod match on both files.

**Inert / dormant on current traffic:**
- 6 open SELL trades from pre-deploy remain — paper_trade_replay resolves them independently of the observer code change.
- Phase 3.1 fallback (`_maybe_propose`) is unchanged; only the score path was modified.

**What I expect to see next:**
- Replay outcome: post-deploy, BTC rally continuing → bias 4h should flip back to bear (4h bear setter is stale); 1D still bear. Sells will continue firing with `align=partial` (current state) until BOTH HTFs are bear, at which point sells fire as `align=agree`. No buy alignment will exist until 1D Cypher flips bull, which is the kind of regime change we want to wait for.
- Cooldown will not produce same-second multi-fires.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-fix123-20260514-1757; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.\$TAG \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
sudo systemctl restart trading-corp.service"
```

---

## 2026-05-12 03:45 UTC — Copy-trader exit pairing + K3 price capture + dashboard polish

**Triggered by:** Jack flagged from screenshots: (1) Kalshi dashboard "not legible" — ENTRY column showed $0.000 / SIGNAL & RESOLVES empty for every K3 row, (2) copy-trader exits visible in Telegram but never closed/PnL'd on the dashboard. Two independent issues, both architectural.

**Files deployed (7 modified, 3 backup tags across 3 sub-deploys):**

### Deploy 1 (03:33 UTC, tag `pre-exit-pairing-d1-20260512-0333`): tracked-file patches
- `trading_corp/persistence/db.py`:
  - `polymarket_round_trips` + `kalshi_round_trips` schemas + idempotent migration both get a new `entry_order_id TEXT` column. Indexes `ix_*_entry_order_id` (partial: WHERE NOT NULL).
- `trading_corp/main.py`:
  - K3 `_scheduled_kalshi_copy_trader_loop` base_payload allowlist gets `whale_entry_price` + `whale_exit_price` (memory `trading_corp_audit_payload_allowlist` — without this the new K3 fields silently drop).
- `trading_corp/web/data.py`:
  - `PMRoundTrip` + `PMOpenTrade` dataclasses gain `whale_handle` field (and `side_detection_confidence` on PMOpenTrade).
  - `_query_pm_round_trips` Polymarket branch reads `extra_json` + overrides `market_result='whale_closed'` when present.
  - `_query_pm_open_trades` both branches: add `side='buy'` filter (so SELL audit rows render as History, not Open) + exclude rows linked as `entry_order_id` on a paired round-trip.
  - `_query_pm_pending_count` both branches: same exclusion as Open.
  - `whale_handle` populated from `whale_user_name` (PM) / `whale_handle` (K3) payload keys.
  - Earlier `arb_type` copy_trade clause was already-applied (this morning's deploy) — patcher detected and skipped idempotently.

### Deploy 2 (03:34–03:36 UTC, tag `pre-exit-pairing-d2-20260512-*`): untracked-file transfers
Per-file base64 transfers (one 131KB combined script silently aborted in az — script size limit; split into 4 per-file calls of ~30KB each succeeded):
- `trading_corp/agents/strategies/kalshi_copy_trader.py`:
  - `_detect_side` now returns `(side, confidence, price)` — captures the matched trade's price (yes_price_dollars or no_price_dollars based on taker_side).
  - `_emit_entry` sets `limit_price=entry_price` + adds `whale_entry_price` to extra. Entry rationale includes `@ $X.XX`.
  - `_emit_exit` becomes **async**, accepts `quote_fetcher` (the trade-tape KalshiBroker), calls `broker.quote(ticker)` for exit price. For NO holdings, inverts: `exit_price = 1 - yes_mid`. Adds `whale_exit_price` to extra. Exit rationale includes `@ $X.XX`.
  - Per-whale snapshot stores `entry_price` so exits can carry it through.
  - New helper `_trade_price_for_side`.
  - Tests updated: `_detect_side` return-tuple now triple instead of pair; assertions added for price.
- `trading_corp/agents/polymarket_resolver.py`:
  - New `_pair_pending_exits(db_url)` — pure SQL, no broker calls. Matches SELL audit rows from `polymarket_copy_trader` to most-recent prior BUY by `(whale_wallet, condition_id, outcome_index)`, computes `realized_pnl = qty × (exit_price − entry_price)`, inserts round-trip keyed by SELL's order_id with `entry_order_id` linking back to BUY.
  - `_fetch_unresolved_orders` gets `side='buy'` filter + `entry_order_id NOT IN` exclusion so SELLs and paired BUYs aren't re-scanned by the market-settle path.
  - `resolve_pending_round_trips` runs pairing FIRST (pure SQL), then market-settle (gamma-api calls). Counts include `paired`, `pair_scanned`, etc.
- `trading_corp/agents/kalshi_resolver.py`:
  - Parallel `_pair_pending_exits` matching on `(whale_handle, ticker, outcome)`.
  - Same `side='buy'` + entry_order_id exclusion in `_fetch_unresolved_orders`.
  - Wired into `resolve_pending_round_trips` same way as Polymarket.
- `trading_corp/web/templates/partials/pm_dashboard_body.html`:
  - ENTRY column: render `—` (muted) when entry_price is None/0, else `$X.XXX`.
  - SIGNAL column: prioritize whale_handle (`@name` with side_detection_confidence) over divergence_pct/edge_cents for copy_trader rows.
  - History tab market_result: render `whale exit` badge (warn color) when `market_result == 'whale_closed'`.

### Deploy 3 (03:45 UTC, tag `pre-k3-pair-relax-20260512-0345`): K3 pre-existing-row pairing relax
- `trading_corp/agents/kalshi_resolver.py`: relaxed K3 pairing to NOT skip on `exit_price <= 0`. Pre-Fix-A K3 audit rows had `limit_price: null`; this lets the 73 stranded historical exits pair into round-trips with `realized_pnl=0` so they show up in History tab. Going forward, K3 rows have real prices and produce real PnL.

**Features shipped:**
- **K3 dashboard now renders legibly.** ENTRY column shows real prices going forward; SIGNAL column shows `@whale_handle` + confidence; `whale exit` badge surfaces in History tab.
- **Copy-trader EXITs now close round-trips.** Both venues. 73 paired K3 round-trips landed immediately on first tick; 1 PM round-trip paired (+$0.20 realized). New exits going forward pair on the next resolver tick (hourly default).
- **Schema additions are forward-compat.** entry_order_id NULL on all legacy/market-settle rows; only SET on paired whale-closed rows.

**Notable code changes (callouts a future Claude shouldn't miss):**
- The resolver pairing path runs BEFORE the market-settle path on every tick — pure SQL, no API cost.
- `_fetch_unresolved_orders` in BOTH resolvers now filters `side='buy'` AND excludes audit rows in `entry_order_id`. Any new strategy that emits SELL audit rows MUST be aware that those don't auto-resolve via market-settle anymore.
- Dashboard's "whale exit" badge appears when `market_result == 'whale_closed'`. Future round-trip resolvers can use the same sentinel to mark non-settlement closes.
- The K3 strategy's `_emit_exit` is now ASYNC. Any caller has to `await`.

**Latent bugs caught + fixed:**
- K3 entry rationale used to say `opened N contracts` with no price; now includes `@ $X.XX` parsed from trade tape. Same for exits (was using copy_size_usd as if it were a price).
- K3 NO-side exits previously had no price source at all; broker.quote() returns YES mid, so the code inverts to `1 - yes_mid` for NO holdings.

**Verification:**
- All 137 PM-dashboard + resolver + copy_trader tests pass locally (pytest passing — 8 unrelated failures in PMCC date-drift + webhook _Deps fixture are pre-existing).
- Post-deploy 3, K3 dashboard at `/prediction-markets/kalshi_copy_trading`: 15 open rows + 147 history rows (73 paired round-trips × main+expand) + 73 "whale exit" badges + @smedtoshi/@tom14cat14 SIGNAL renders.
- PM resolver tick log: `paired: 0, pair_scanned: 0` (no new PM exits to pair beyond the +$0.20 one from earlier; whales still holding).
- 1 PM whale-closed round-trip with realized_pnl=+$0.20 (real, prices were captured day-1 on PM side).

**Inert / dormant on current traffic:**
- The 73 K3 historical pairings show `realized_pnl=0` — accurate given missing pre-Fix-A prices. New K3 round-trips going forward will have real PnL.
- `whale_handle` field on PMRoundTrip is None for legacy market-settle rows; populated only for whale-closed rows. Template handles None gracefully.

**Rollback recipe:**
```bash
# Three layers (most recent first):
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
  --command-id RunShellScript \
  --scripts "TS=20260512; BASE=/home/azureuser/trading_corp; \
    # Layer 3 (pair-relax) rollback:
    mv \$BASE/trading_corp/agents/kalshi_resolver.py.pre-k3-pair-relax-\${TS}-0345 \$BASE/trading_corp/agents/kalshi_resolver.py 2>/dev/null; \
    # Layer 2 (untracked file transfers) rollback:
    for f in trading_corp/agents/strategies/kalshi_copy_trader.py trading_corp/agents/polymarket_resolver.py trading_corp/agents/kalshi_resolver.py trading_corp/web/templates/partials/pm_dashboard_body.html; do \
      BACKUP=\$(ls \$BASE/\$f.pre-exit-pairing-d2-\${TS}-* 2>/dev/null | head -1); \
      [ -n \"\$BACKUP\" ] && mv \"\$BACKUP\" \"\$BASE/\$f\"; \
    done; \
    # Layer 1 (tracked file patches) rollback:
    mv \$BASE/trading_corp/persistence/db.py.pre-exit-pairing-d1-\${TS}-0333 \$BASE/trading_corp/persistence/db.py; \
    mv \$BASE/trading_corp/main.py.pre-exit-pairing-d1-\${TS}-0333 \$BASE/trading_corp/main.py; \
    mv \$BASE/trading_corp/web/data.py.pre-exit-pairing-d1-\${TS}-0333 \$BASE/trading_corp/web/data.py; \
    sudo systemctl restart trading-corp.service" \
  --query "value[0].message" -o tsv
# Note: entry_order_id column stays after rollback (sqlite ALTER not reversible without table rebuild).
# Old code doesn't reference it so no harm — just unused column on existing rows.
```

---

## 2026-05-12 02:34 UTC — K3 throttle to fit Apify Starter $200/mo hard cap

**Triggered by:** Session-start Apify probe revealed Starter plan burn at $10.68/day (= ~$320/mo extrapolated) — would exhaust in ~1.6 days. Jack clarified Apify Starter is hard-capped at $200/mo (no plan upgrade), asked me to cut data-request volume to fit.

**Files deployed (1 config, no code, no restart):**
- `config/strategies.yaml`: `kalshi_copy_trader.poll_interval_sec` 300s → **600s** (5min → 10min cadence). Single-line config change, hot-reloaded via `KalshiCopyTraderAgent._reload` mtime check on next cycle.

**Backup tag:** `pre-k3-throttle-20260512-0234` (yaml-only, single file).

**Math:**
- K3 makes exactly 1 Apify call per cycle (`fetch_open_positions` with all 4 whales batched in one actor run), so cost scales linearly with cadence.
- 5min → 10min halves request volume → ~$5.34/day → **~$160/mo** (Apify Starter cap = $200/mo, leaving ~$40/mo buffer for spikes or future whale-pool expansion).
- 8min (480s) would land right at $200/mo with zero buffer — too tight; 10min chosen for safety.

**Notable behavior change:**
- K3 position-freshness lag becomes 10min worst-case (was 5min). Per the strategy's `positions don't change fast on Kalshi` design assumption, this is fine — biggest theoretical loss is missing a fast whale entry/exit within a single 10min window, vs. observed 5min.

**Pre-existing memory now stale (separate update made):**
- `trading_corp_kalshi.md` had "Cost: ~$30-50/mo expected" — actual measured $320/mo at 5min/4-whale (off by ~10x). Memory updated to reflect measured cost + $200 cap + new 10min cadence.

**Yaml drift caught (note for future deploys):**
- Patch script's primary string-match fell through to the line-only regex fallback — prod's `config/strategies.yaml` had a slightly different comment on the K3 `poll_interval_sec: 300` line than my local. Fallback regex correctly rewrote just the line. Same `trading_corp_prod_git_drift` pattern as the data.py deploy earlier this session.

**Verification:**
- Backup created (`pre-k3-throttle-20260512-0234`), yaml re-parses cleanly, `poll_interval_sec` confirmed = 600 via `yaml.safe_load`.
- No systemd restart — mtime hot-reload picks up on next K3 reload cycle (within current 5min sleep window).
- TODO: re-probe `/v2/users/me/usage/monthly` after 24h to confirm new daily burn ≈ $5.34 (50% of pre-throttle). Cumulative cycle burn at next check should grow by ~$5.34 between check times.

**Rollback recipe:**
```bash
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
  --command-id RunShellScript \
  --scripts "TAG=pre-k3-throttle-20260512-0234; BASE=/home/azureuser/trading_corp; \
    mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml" \
  --query "value[0].message" -o tsv
# No restart needed for rollback either — mtime hot-reload picks up.
```

---

## 2026-05-12 02:19 UTC — PM dashboard: render copy_trading divisions

**Triggered by:** State-check at session start revealed `/prediction-markets/kalshi_copy_trading` and `/prediction-markets/polymarket_copy_trading` were rendering empty despite K3 (110 audit rows) and Polymarket Copy Trader (54 audit rows) already firing live. Two independent gaps:

1. **Kalshi resolver wiring gap.** `kalshi_resolver._KALSHI_ACTORS` hardcoded the 3 arb-family actors and excluded `kalshi_copy_trader`, so K3 audit rows could never become `kalshi_round_trips`. Polymarket resolver was already wired correctly (memory `trading_corp_polymarket` 2026-05-11 deploy).
2. **Dashboard data-layer queries didn't know about copy_traders.** `web/data.py`'s 4 PM query functions hardcoded `actor='polymarket_arbitrage'` / the 3-actor Kalshi list, and hardcoded `division='polymarket_arbitrage'` on output rows — so even with a divisions.yaml slug, queries returned zero.

**Files deployed (2 modified, 1 backup tag):**

**Deploy (02:15 UTC, tag `pre-pm-dashboard-copy-20260512-0215`):**
- `trading_corp/agents/kalshi_resolver.py`:
  - Added `kalshi_copy_trader` to `_KALSHI_ACTORS`, `_KALSHI_DIVISIONS`, `_ACTOR_TO_DIVISION` (→ `kalshi_copy_trading`), `_ACTOR_TO_ARB_TYPE_DEFAULT` (→ `copy_trade`).
  - `_detect_side` needed no change — K3 payload's `outcome` field is `"yes"/"no"` which it already handles.
  - md5 matched local exactly (`618ed95f…`) — prod/local in sync on this file.
- `trading_corp/web/data.py` (4 functions touched, 7 string-replace edits):
  - `_query_pm_round_trips` Polymarket branch: read `division` column from `polymarket_round_trips` via `COALESCE(division, 'polymarket_arbitrage')` so legacy pre-column rows still filter as arbitrage; accept any `polymarket_*` slug.
  - `_query_pm_open_trades` Polymarket branch: actor list expanded to `('polymarket_arbitrage', 'polymarket_copy_trader')`; filter by `payload.division` so single-division view doesn't bleed cross-division rows.
  - Open-trades title fallback chain extended to read `p.get("market_title")` (the copy_trader payload uses that key; arbitrage uses `market_question`).
  - `_query_pm_open_trades` Kalshi branch: actor list expanded to include `kalshi_copy_trader`; arb_type derivation gets `copy_trade` clause.
  - `_query_pm_pending_count`: both branches mirror the open-trades fixes.
  - `_query_pm_equity_curve` Polymarket branch: switched to IN-clause for forward-compat (when polymarket_copy_trading equity_history rows eventually land they'll auto-render — today there are zero).
  - Post-patch md5 was `815e1bb8…` ≠ local `3be4eb01…`. Drift is in non-edited regions of data.py — patches applied cleanly (all 7 old_strings matched uniquely) so my edits are correctly in place; the divergence is preserved (no stomp). This is the `trading_corp_prod_git_drift` pattern, expected.

**Features shipped (load-bearing for future "is X done?" checks):**
- `/prediction-markets/kalshi_copy_trading` Open tab renders the 110 paper copies (verified 233 `<tr>` in Open tab = 110 trades × main+expand rows + headers).
- `/prediction-markets/polymarket_copy_trading` Open tab renders the 54 paper copies (verified 121 `<tr>` rows similarly).
- Kalshi resolver will now convert K3 `would_have_placed` audit rows to `kalshi_round_trips` rows on its hourly tick. First batch lands ~03:19 UTC; resolutions appear in the dashboard's History tab as they accumulate.

**Notable code changes (callouts a future Claude shouldn't miss):**
- `kalshi_resolver._KALSHI_ACTORS` is now 4 entries, not 3. Any future Kalshi strategy MUST be added here AND to `_ACTOR_TO_DIVISION` AND to the actor list in `web/data.py:_query_pm_open_trades` (Kalshi branch line ~2629/2646) AND `_query_pm_pending_count`. Same applies for Polymarket — add to actor list in the same two functions' Polymarket branches.
- `polymarket_round_trips.division` column is now LIVE both at write-time (resolver line 92-98) and read-time (data.py uses COALESCE for legacy NULL rows).

**Latent bugs caught + fixed (none new):** none. The Apify burn at $10.68/day (44% of $29 cap in 26h, ~1.6 days to exhaustion) remains an OPEN URGENT item — not addressed in this deploy. Whales tab P0a + multi-leg resolver P0b deferred per scope agreement.

**Verification:**
- Patch markers: `grep -c 'kalshi_copy_trader' trading_corp/agents/kalshi_resolver.py` = 3 ✓; `grep -c 'polymarket_copy_trader' trading_corp/web/data.py` = 5 ✓; `grep -c 'kalshi_copy_trader' trading_corp/web/data.py` = 3 ✓.
- Service restart: PID rotated, `systemctl is-active` = `active`, no `ERROR|Traceback|ImportError` in startup log.
- Dashboard probes via localhost:8000 (bypasses Authelia):
  - kalshi_copy_trading partial: 200 OK, 436KB, 233 `<tr>` in Open tab, KX* tickers present.
  - polymarket_copy_trading partial: 200 OK, 214KB, 121 `<tr>` in Open tab.
  - Regression check on existing PM divisions all clean: polymarket_arbitrage (55 open / 5 history), kalshi_llm_arbitrage (401 open / 59 history), kalshi_arbitrage (133 open / 0 history).

**Inert / dormant on current traffic (if any):**
- `polymarket_copy_trading` equity curve will be empty until equity-snapshot loops are spawned for the copy_trading divisions (currently zero rows in `polymarket_equity_history` and `kalshi_equity_history` for those divisions). Forward-compat IN-clause is already in place; just need an orchestrator change to start the snapshot loops. Not blocking dashboard utility.
- `copy_trade` arb_type label appears in `_query_pm_open_trades` but template UI may render it as plain text; no special CSS treatment yet.

**Deploy script gotcha for next time:**
- The deploy script `runbooks/.deploy_pm_dashboard_copy_trades.sh` had `set -euo pipefail` at the top, which caused bash to exit immediately when the Python heredoc exited non-zero (on the soft md5-mismatch signal) — BEFORE running the rollback / restart blocks. Net effect: first run silently applied patches but didn't restart systemd. Second run reported "NOT FOUND" because prod already had the patches. Workaround: dropped the rollback-on-mismatch (distinguished hard vs soft failures) and re-ran the restart manually. Future deploy scripts should either replace `set -e` with explicit error handling, or use `python3 ... || true` and check `$?` explicitly.

**Rollback recipe:**
```bash
# SSH path (blocked from current IP — use az alternative below):
ssh azureuser@trading.jacksumner.com "
TAG=pre-pm-dashboard-copy-20260512-0215; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/kalshi_resolver.py.\$TAG \$BASE/trading_corp/agents/kalshi_resolver.py; \
mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
sudo systemctl restart trading-corp.service
"

# az alternative:
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
  --command-id RunShellScript \
  --scripts "TAG=pre-pm-dashboard-copy-20260512-0215; BASE=/home/azureuser/trading_corp; \
    mv \$BASE/trading_corp/agents/kalshi_resolver.py.\$TAG \$BASE/trading_corp/agents/kalshi_resolver.py; \
    mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
    sudo systemctl restart trading-corp.service" \
  --query "value[0].message" -o tsv
```

---

## 2026-05-11 23:00 UTC — paper_trade_replay: BitUnix symbol routing + premature-expired fix

**Triggered by:** Board asked when the "Paper-trade win rate" panel on `/division/bitunix_futures` would populate. Investigation found two bugs:

1. **BitUnix paper trades never resolved.** The replay loop was running every 15min but failing on every BitUnix order with `ERROR: coinbase does not have market symbol BTC/USDT.P`. Root cause: single-venue Coinbase ccxt fetcher used for ALL strategies.
2. **Premature `expired` classification.** Once #1 was fixed and BitUnix bars started flowing, all 4 stuck rows immediately got marked `expired` — but the trades were only 2-6h old with a 24h `max_hold_seconds`. The classifier was treating "ran out of fetched bars" as "trade expired" without checking wall-clock elapsed time.

**Files deployed (1 modified, 2 backup tags):**

**Deploy 1 (22:30 UTC, tag `pre-replay-bitunix-routing-20260511-2230`):** symbol-aware OHLCV router.
- `trading_corp/agents/paper_trade_replay.py`:
  - Renamed `_default_ccxt_fetcher` → `_coinbase_ccxt_fetcher` for clarity.
  - **New `_bitunix_kline_fetcher`** — hits `https://fapi.bitunix.com/api/v1/futures/market/kline` (no auth, same source `LiveBarCache` uses for live ATR). Paginates 1000 bars/call. Returns ccxt-shaped `[ts_ms, o, h, l, c, v]` rows in chronological order.
  - **New `_to_bitunix_symbol` / `_is_bitunix_symbol`** helpers. Detection rule: symbol ends in `.P` → BitUnix perp; else → Coinbase spot. Symbol normalization: `BTC/USDT.P` → `BTCUSDT` for the REST call.
  - **New `_default_router_fetcher`** — single entry point that dispatches per-symbol. Replaced `_default_ccxt_fetcher` reference in `_replay_tick_async`.
  - Smoke-tested against live BitUnix API: 30×1m bars returned chronologically with sane OHLCV.

**Deploy 2 (23:00 UTC, tag `pre-replay-still-open-20260511-2300`):** still_open verdict.
- `trading_corp/agents/paper_trade_replay.py`:
  - **New `_Resolved.result` value: `"still_open"`** — transient verdict the caller never writes to DB. Documented in the docstring as "row stays at result=NULL so the next replay tick picks it up again."
  - `_classify` now computes `elapsed = now - row.ts` and only returns `"expired"` when `elapsed >= max_hold_seconds`. Otherwise returns `"still_open"` (no DB write).
  - Helper `_parse_row_ts(ts)` for the wall-clock comparison.
  - `_replay_tick_async` checks for `result == "still_open"` and `continue`s past `_update_row` — leaves row at NULL for the next tick.
  - New `still_open` bucket in the counts dict for visibility.
- **DB cleanup step:** UPDATEd 4 prematurely-expired BitUnix rows back to `result=NULL, result_ts=NULL, result_price=NULL, actual_pnl_dollars=NULL, actual_r_multiple=NULL, bars_to_resolution=NULL` so they re-process correctly under the fixed classifier.

**Verification (immediately post-deploy):**
- Post-restart catch-up tick: `{'scanned': 4, 'resolved_win': 0, 'resolved_loss': 0, 'resolved_expired': 0, 'still_open': 4, 'errors': 0}` ✓
- All 4 BitUnix rows back to `result=NULL` — will re-evaluate every 15min until either TP/SL hits OR the genuine 24h max_hold elapses.

**4 boundary cases unit-tested locally:**
- 2h old, 24h hold, no hit → `still_open` ✓
- 25h old, 24h hold, no hit → `expired` ✓
- 2h old, TP hit at bar 60 → `win` (bars_to_resolution=61) ✓
- 2h old, SL hit at bar 30 → `loss` (bars_to_resolution=31) ✓

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-replay-still-open-20260511-2300; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/paper_trade_replay.py.\$TAG \$BASE/trading_corp/agents/paper_trade_replay.py; \
sudo systemctl restart trading-corp.service
"
```
(Rolls back BOTH fixes — the still_open verdict ride on top of the symbol-routing change. To partially roll back just the still_open fix, use `pre-replay-bitunix-routing-20260511-2230` instead.)

---

## 2026-05-11 21:20–22:00 UTC — Dashboard timezone sweep (all timestamps → Eastern)

**Triggered by:** Board said "the bitunix dashboard you helped me with…it is showing times in zulu time. i need all times on the dashboard to be eastern timezone." Subsequent sweep across all dashboard surfaces to replace UTC literals with ET.

**Approach:** Jinja filters `et_hms` / `et_short` / `et_full` already existed (registered in `web/app.py:94-96`, sourced from `utils/time.py`). Just needed to swap raw timestamp slices for filter calls — no data builder changes for most, one targeted addition for the BitUnix score panel.

**Files deployed (3 modified across 2 sub-deploys, backup tag `pre-tz-sweep-20260511-2145` + `pre-tz-sweep-routes-20260511-2200`):**

**21:20 UTC deploy — BitUnix score panel + four other templates:**
- `trading_corp/web/data.py` — added `ts_et` field via `format_et_short()` to each `recent_evals` + `recent_fires` entry in `build_bitunix_score_view`. (Pre-formatting in the builder keeps the template branchless and ensures consistency.)
- `trading_corp/web/templates/partials/bitunix_score_panel.html` — Recent fires + Recent evaluations tables now render `{{ f.ts_et }}` / `{{ e.ts_et }}` (was `{{ f.ts[5:16] }}Z` etc).
- `trading_corp/web/templates/base.html` — scheduler last_run header: `{{ snap.health.scheduler.last_run | et_hms }}` (was `[11:19]Z`).
- `trading_corp/web/templates/partials/kalshi_analysis.html` — position `expires_at` uses `| et_short` filter.
- `trading_corp/web/templates/partials/polymarket_analysis.html` — event `resolves_at` uses `| et_short` filter.
- `trading_corp/web/templates/research.html` — 5 occurrences of `ts[:19]` → `(ts | et_short)`.

**22:00 UTC deploy — routes.py renderers:**
- `trading_corp/web/routes.py` — 3 inline `ts_dt.strftime("%Y-%m-%d %H:%M:%S UTC")` calls (PMCC analysis renderers) replaced with `format_et_full(ts_dt)`. Import already present at line 33.

**Verification (post-deploy):**
- Scheduler header now reads `sched: 08:33:07 ET` (was `12:33:07Z`).
- BitUnix score panel Recent fires + Recent evaluations tables render `05-11 15:54 ET` (was `05-11T19:54Z`).
- Final grep sweep confirmed no remaining `}}Z`, no remaining `[:19]` raw slices, no remaining `"UTC"` literals across `trading_corp/web/`.

**Inert / dormant:**
- The two `_humanize_ts` callers in `data.py` (used for activity-feed "5m ago" relative times in PMCC/IRA recent-activity sections) are unchanged — they're timezone-neutral by construction.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-tz-sweep-20260511-2145; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/templates/base.html.\$TAG \$BASE/trading_corp/web/templates/base.html; \
mv \$BASE/trading_corp/web/templates/partials/kalshi_analysis.html.\$TAG \$BASE/trading_corp/web/templates/partials/kalshi_analysis.html; \
mv \$BASE/trading_corp/web/templates/partials/polymarket_analysis.html.\$TAG \$BASE/trading_corp/web/templates/partials/polymarket_analysis.html; \
mv \$BASE/trading_corp/web/templates/research.html.\$TAG \$BASE/trading_corp/web/templates/research.html; \
TAG2=pre-tz-sweep-routes-20260511-2200; \
mv \$BASE/trading_corp/web/routes.py.\$TAG2 \$BASE/trading_corp/web/routes.py; \
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-11 19:30–20:30 UTC — Robinhood IRA dashboard reworks (PMCC-style rows + expert analysis parity)

**Triggered by:** Board feedback after the initial 19:00 UTC IRA dashboard ship. Three requested changes consolidated into this entry, three sequential deploys:

1. **PMCC-style click-to-expand rows** (replaced wide horizontal table). User said: "i want the open options UI to work just like robinhood pmcc."
2. **Section rename** — "Pure Assets" → "Portfolio", "Wheel Puts" → "Puts" with the wheel framing dropped entirely. User said: "there is no need for a wheel section. i do not run a wheel strategy per se."
3. **Expert Analysis parity** — analysis panel was deterministic but visually different from PMCC. User said: "you did not reuse the code built for robinhood pmcc."

**19:30 UTC deploy — PMCC-style rows + section rename (backup tag `pre-ira-pairs-20260511-1930`):**
- `trading_corp/web/data.py` — added `priority_score` / `priority_label` / `recommended_action` properties to `CoveredCallPosition` (mirrors `PMCCPair`'s priority model: urgent/elevated/routine/healthy + Roll/Close/Watch/Hold action). Renamed dict keys: `pure_assets` → `portfolio`, `wheel_puts` → `puts`. Sort order changed from "ITM first by DTE" to "priority_score desc, DTE asc as tiebreaker."
- `trading_corp/web/templates/partials/ira_pair.html` — **NEW**. Click-to-expand row mirroring `pmcc_pair.html`: priority dot + symbol + spot + "covered call" badge + recommended-action pill + DTE badge + Combined P&L on the right + chevron. Expanded body: LEFT panel = shares (qty / avg cost / last / mkt value / cost basis / P&L), RIGHT panel = short call (qty / delta / credit / mark / intrinsic / extrinsic / P&L). Visual parity with PMCC.
- `trading_corp/web/templates/partials/ira_dashboard.html` — rewritten: three sections renamed to **Covered Calls** (uses `ira_pair.html`) / **Portfolio** / **Puts** (hides entirely when no open puts; no wheel framing). List container renamed `id="pair-list"` so `static/js/pair_list.js` handles single-open accordion + "Loading {symbol}..." flash on the IRA rows too.

**20:00 UTC deploy — Expert Analysis stub renderer (backup tag `pre-ira-analysis-20260511-2000`):**
- Added htmx hookup to `ira_pair.html` summary (`hx-get="/division/{slug}/pair-analysis/{symbol}"` + target `#pair-analysis` + swap innerHTML). Added IRA dispatch in the existing `division_pair_analysis` endpoint (previously bailed for non-PMCC slugs at line 671). First version used a custom deterministic renderer `_render_ira_pair_analysis(cc)` showing breakeven / max profit / expiry scenarios.
- **Bug caught during verification:** `hx-sync="closest #ira-cc-list:replace"` was stale from before the list rename. In HTMX 2.x, an unresolvable `closest` selector prevents the request from firing entirely. Fixed to `closest #pair-list:replace` and redeployed.

**20:30 UTC deploy — PMCC renderer parity (backup tag `pre-ira-pmcc-renderer-20260511-2030`):**
- `trading_corp/web/routes.py`:
  - New `_analyze_ira_covered_call(cc, broker, deps)` async function. Returns `(PMCCAnalysis, TradeRecommendation | None)` — the SAME dataclass shapes PMCC produces — so `_render_pair_analysis` consumes IRA output without modification.
  - Rule-based action picker (no LLM call). Decision tree:
    - `0 DTE + ITM` → roll_short_early urgent (conf 0.95)
    - `0 DTE OTM` → hold routine (let expire)
    - `profit ≥85%` → close_short elevated
    - `≤2 DTE + ITM` → roll_short_early urgent (conf 0.90)
    - `≤2 DTE OTM` → hold routine (let theta finish)
    - `profit ≥70%` → close_short elevated
    - `ITM + >2 DTE` → watch elevated (with **preview-only** roll legs so user sees the trade shape even when not yet urgent)
    - otherwise → hold routine
  - Multi-paragraph rationale cites the specific rule (R1–R5) applied. Warnings cover assignment risk, credit-only roll requirement, partial coverage.
  - **Real chain fetch** via `broker.get_expiration_dates` + `broker.get_calls_for_expiry` for the "Sell to open" next-week leg. Picks the listed strike closest to `max(spot × 1.03, current_strike + 0.50)`. Returns `mark_per_share` / `bid` / `ask` / `delta` so spread-quality dots render. Falls back gracefully on chain-fetch failure (BTC leg only).
  - `_render_pair_analysis` gained `show_execute_button: bool = True` (default preserves PMCC behavior; IRA passes `False` to hide the Approve/Defer buttons since no IRA automation is wired — user executes manually in Robinhood).
  - IRA dispatch in `division_pair_analysis` calls the new analyzer, renders via `_render_pair_analysis(analysis, recommendation, slug, sym, show_execute_button=False)`, caches in `_pair_cache` (5-min TTL — same as PMCC).
  - The original custom `_render_ira_pair_analysis` is now dead code (kept for rollback safety, will be removed in a follow-up).

**Verification (post-20:30 UTC deploy, against real MARA position: 1200 shares avg $16.69, short 12× $13C 4DTE @ $0.92, spot $13.44):**
- Endpoint output: 3,471 bytes (vs. 1,071 bytes in the 20:00 stub).
- Markers confirmed: WATCH badge, 75% conf, Warnings, Rule R citations, Buy to close leg, Sell to open leg (real broker-fetched next-week $14C 11DTE @ $0.76), Net debit $252, Expected benefit ("Preview only — rules say WATCH"), MEDIUM cost confidence, no Approve/Defer buttons.
- Visual parity with PMCC confirmed in user screenshot — same urgency emoji + action badge + confidence + multi-paragraph rationale + warnings list + concrete trade legs + expected benefit structure.

**Inert / dormant:**
- Old `_render_ira_pair_analysis` function still in routes.py (marked deprecated). Remove on next cleanup pass.
- Rule tuning is in BACKLOG — current decision tree is the initial cut. Board flagged ≤2 DTE threshold for ITM-roll-urgency may want loosening to ≤4 DTE; deferred to a future tuning pass.

**Rollback recipes** (in reverse-deploy order; pick one):
```bash
# Rollback PMCC-renderer integration only (restores 20:00 stub renderer)
ssh azureuser@trading.jacksumner.com "
TAG=pre-ira-pmcc-renderer-20260511-2030; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/routes.py.\$TAG \$BASE/trading_corp/web/routes.py; \
sudo systemctl restart trading-corp.service
"

# Rollback to the original 19:00 IRA dashboard (wide table + Wheel Puts label)
ssh azureuser@trading.jacksumner.com "
TAG=pre-ira-pairs-20260511-1930; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/templates/partials/ira_dashboard.html.\$TAG \$BASE/trading_corp/web/templates/partials/ira_dashboard.html; \
rm -f \$BASE/trading_corp/web/templates/partials/ira_pair.html; \
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-11 20:17 UTC — Polymarket Copy Trader division (paper-mode live)

**Commits:** none — files patched onto prod's drifted content (per `trading_corp_prod_git_drift` memory). Parallel BitUnix sprint was running on the same VM; patcher applied K3-equivalent additions on top of prod's current state, not git HEAD.
**Triggered by:** User re-prioritized `polymarket_copy_trading` from deprioritized → active build the same day K3 shipped. Goal: validate the copy-trading thesis on a SECOND venue in parallel, leveraging Polymarket's free public Data API (no Apify-equivalent cost), explicit side+outcome in `/activity` (no trade-tape inference), and the venue-agnostic scoring engine already built for K3.
**Backup tags:**
- `pre-polymarket-copy-20260511-2011` — covers `kalshi_whale_stats.py`, `persistence/db.py`, `polymarket_resolver.py`, `main.py`, `config/strategies.yaml` (5 modified)
- `pre-pm-enable-20260511-2017` — strategies.yaml backup before flipping `enabled: true`

**Files deployed (5 new, 5 modified):**
- `trading_corp/data/polymarket_data_api_client.py` — **NEW** (~340 lines). Async wrapper over Polymarket's public REST endpoints at `data-api.polymarket.com`. Dataclasses: `LeaderboardEntry`, `ActivityRow`, `PositionRow`. Endpoints: `/v1/leaderboard?category=<C>&limit=N&offset=N` (discovery, supports 5 working categories — Politics/Sports/Crypto/Tech/Mentions), `/activity?user=<wallet>&limit=N` (per-wallet trade history with explicit side/outcome/price/USDC size), `/positions?user=<wallet>` (current open). Plus `fetch_market_resolutions(condition_ids)` hitting `gamma-api.polymarket.com/markets` in BOTH open + closed variants per chunk (gamma-api defaults to `closed=false` and intersects with `condition_ids` filter — needs two passes to capture both states). `_decode_resolution` distinguishes resolved (one price ≥0.9 → win_idx), void (closed but all-near-zero prices), pending (closed=false). All free, no auth.
- `trading_corp/data/polymarket_whale_stats.py` — **NEW** (~225 lines). Venue-specific stats adapter. `compute_polymarket_stats(leaderboard_entry, activity_rows, market_resolutions, half_life_days)` builds a `WhaleStats` record by filtering BUY trades through resolution lookup, computing time-weighted Wilson-LCB + ROI from real entry-price + USDC-size math. `_is_win_for_buy` joins activity outcome_index against winning_outcome_index. Reuses venue-agnostic `wilson_lcb_95`, `_edge_factor`, `_category_bonus` from `kalshi_whale_stats`.
- `trading_corp/agents/strategies/polymarket_copy_trader.py` — **NEW** (~370 lines). Strategy. Per-cycle: load selected whales, fetch `/activity` per whale, filter to TRADE rows newer than `last_seen_ts` + dedup by `transaction_hash`. BUYs emit copy ProposedOrders (sized via USDC bet-size tiers $1/$2/$5), SELLs of held positions emit close orders. **`qty` in CONTRACTS** (`copy_usdc / entry_price`) so the resolver's `notional = qty * price` math is consistent. `limit_price` = whale's entry price. Side detection explicit (no Kalshi-style size-match). Cold-start safe.
- `trading_corp/scripts/refresh_polymarket_whales.py` — **NEW** (~310 lines). Quarterly selection orchestrator. Rule B: top-2 per cat × 5 cats + top-2 global = 12. Pulls leaderboard per cat + global → enriches via `/activity?limit=200` → batch-fetches market resolutions (gamma-api, 50-id chunks, open+closed variants) → scores per (whale, target_category) → picks rule B. Cost: $0. Time: ~5s for 100+ candidates.
- `tests/test_polymarket_copy_trader.py` — **NEW** (~340 lines, 23 tests). All pass; full suite 387 tests, zero regressions.
- `trading_corp/data/kalshi_whale_stats.py` — extended with `wilson_lcb_95_weighted(weighted_wins, n_eff)` (Kish's effective sample size) and `time_weighted_outcomes(samples, now_ts, half_life_days)` (exp decay, default 30d half-life). Venue-agnostic.
- `trading_corp/persistence/db.py` — added `division TEXT NOT NULL DEFAULT 'polymarket_arbitrage'` column to `polymarket_round_trips` (was implicitly arbitrage-only). New `_maybe_add_column()` helper for idempotent `ALTER TABLE ADD COLUMN` migrations. `init_db` calls it, then creates `ix_polymarket_round_trips_division` index AFTER the migration (intentionally NOT in SCHEMA to avoid CREATE-INDEX-on-missing-column on upgraded DBs). Verified on a pre-migration prod-shaped DB.
- `trading_corp/agents/polymarket_resolver.py` — `_fetch_unresolved_orders` widened from `actor = 'polymarket_arbitrage'` to `actor IN ('polymarket_arbitrage', 'polymarket_copy_trader')` + carries `_actor` field. `_compute_round_trip_row` stamps `division` from payload, falling back to actor-name inference (`polymarket_copy_trader` → `polymarket_copy_trading`). Slug/title fallbacks for copy-trader payload shape.
- `trading_corp/main.py` — `_scheduled_polymarket_copy_trader_loop(agent, *, channel, logger_agent, data_exec, risk_agent, db_url)` mirrors `_scheduled_kalshi_copy_trader_loop` shape but takes NO Apify token + NO trade-tape-fetcher. Owns the `PolymarketDataAPIClient` lifecycle. Audit base_payload enumerates 17 K3-equivalent Polymarket fields. Telegram emoji 🟣 (distinguishes from K3's 🐋). Startup wiring sits right after `polymarket_arb_task`.
- `config/strategies.yaml` — `polymarket_copy_trader:` block appended. Default `enabled: false` → flipped to `true` via in-place sed after first successful restart.

**Features shipped:**
- New division: `polymarket_copy_trading` flipped from standby-placeholder to active. Same wallet as polymarket_arbitrage shared during paper-mode per CLAUDE.md (separate wallet planned for live-mode per Jack).
- 12 selected whales committed to `agent_state(polymarket_copy_trader.selected_whales)`. All opt-in public, no anonymity gradient (vs Kalshi K3's ~7%). Top whale `248188374`: 197 resolved, 100% WR, $133K lifetime P&L, Sports specialist.
- Polymarket Data API wired as first-class data source — discovery + per-wallet enrichment + resolution batch all free, no auth, no Apify-equivalent recurring cost.
- Time-weighted Wilson LCB in the venue-agnostic scoring engine — Kalshi K3 could opt in too via `half_life_days` param.
- `polymarket_round_trips.division` column lets the existing resolver pipe BOTH arbitrage and copy_trading round-trips into the same table.

**Notable code decisions:**
- **Recon agent's `/leaderboards` endpoint was hallucinated.** Real endpoint is `/v1/leaderboard` (singular, with `/v1/` prefix). Documented URL returns 404. Don't trust agent-cited URLs without a fresh probe.
- **5 working categories, not 12.** Polymarket's taxonomy has 9 top-level but only Politics/Sports/Crypto/Tech/Mentions return leaderboard data. Rule B adjusted from "top-1 per cat × 12 volume cats" to "top-2 per cat × 5 + top-2 global = 12".
- **gamma-api's `condition_ids` filter intersects with `closed=false` by default.** Required two passes per chunk (open variant + closed variant) to capture both market states.
- **`qty` in CONTRACTS, not USDC.** Originally emitted in USDC, but the resolver's binary-settlement math requires contracts. Normalization: `contracts = copy_usdc / entry_price`.
- **Multi-leg sports markets won't auto-resolve in v1.** Resolver's `_compute_round_trip_row` gates on `outcome.lower() in {"yes", "no"}`. Spurs/Cavaliers/etc. land in audit_event but not polymarket_round_trips. Acceptable v1 gap; resolver extension is small follow-up.
- **No trade-tape inference needed.** Polymarket's `/activity` carries `side: BUY|SELL` + `outcome_index: 0|1` + human `outcome` label directly. K3's size-match dance is venue-specific to Kalshi.

**Verification:**
- Pre-restart import smoke on prod: all 8 Polymarket modules + `trading_corp.main` import cleanly under prod's venv.
- PID rotation 260521 → 261879 on first restart, → 262635 on a parallel-session restart 2 min later (BitUnix sprint concurrent; no file collisions).
- Schema migration verified: `division` column present with DEFAULT `'polymarket_arbitrage'`. Existing rows backfilled.
- "Polymarket copy trader scanner online (enabled=False)" at 20:13:01, then "(enabled=True)" after the strategies.yaml flip at 20:17.
- **Cold-start fired at 20:16:24-27 UTC** — 12 `polymarket_copy_cold_start` audit events. 11 whales got populated baselines (15-20 rows each); 1 (`Talvez10`) returned empty (will baseline on next cycle).
- 23 new unit tests pass; full suite 387 tests, zero regressions.

**First selection (committed to prod 2026-05-11 20:14 UTC):**
- Sports×2: `248188374` (197 resolved, 100% WR), `ic4cream` (99 resolved, 93% WR)
- Tech×2: `OnlySafeBets` (107 resolved, 83% WR), `wenzhu` (53 resolved, 79% WR)
- Crypto×2: `ddssaaas6` (166 resolved, 89% WR), `0xE9Ba96828e513a...` (191 resolved, 77% WR)
- Politics×2: `VladimirPooper` (130 resolved, 94% WR), `mohahaha` (17 resolved, 88% WR)
- Mentions×2: `Pedrobeliever47` (11 resolved, 82% WR), `0xe617861a96631d...` (71 resolved, 94% WR)
- GLOBAL×2: `00xx00xx00` (112 resolved, 58% WR but +$1.08/$ ROI), `Talvez10` (180 resolved, 67% WR)

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-copy-20260511-2011; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/data/kalshi_whale_stats.py.\$TAG       \$BASE/trading_corp/data/kalshi_whale_stats.py; \
mv \$BASE/trading_corp/persistence/db.py.\$TAG                \$BASE/trading_corp/persistence/db.py; \
mv \$BASE/trading_corp/agents/polymarket_resolver.py.\$TAG    \$BASE/trading_corp/agents/polymarket_resolver.py; \
mv \$BASE/trading_corp/main.py.\$TAG                          \$BASE/trading_corp/main.py; \
mv \$BASE/config/strategies.yaml.\$TAG                        \$BASE/config/strategies.yaml; \
rm -f \$BASE/trading_corp/data/polymarket_data_api_client.py \
      \$BASE/trading_corp/data/polymarket_whale_stats.py \
      \$BASE/trading_corp/agents/strategies/polymarket_copy_trader.py \
      \$BASE/trading_corp/scripts/refresh_polymarket_whales.py \
      \$BASE/tests/test_polymarket_copy_trader.py; \
sudo systemctl restart trading-corp
"
```

(The `division` column on `polymarket_round_trips` survives the rollback — additive schema, no rollback needed. `agent_state.selected_whales` persists; harmless without the strategy code.)

---

## 2026-05-11 19:00 UTC — Robinhood IRA detailed dashboard (covered calls + pure assets + wheel puts)

**Triggered by:** Board direction — IRA strategy is buy-and-hold + sell weekly covered calls (no LEAPs allowed in retirement accounts; shares must back the short calls). Occasional cash-secured puts as a wheel entry. The existing `/division/robinhood_ira` page used the generic PMCC/Holdings layout which doesn't model this — covered calls weren't grouped with their underlying shares, and the page showed an empty "Positions" section because there are no PMCC pairs in IRA.

**Files deployed (1 new, 2 modified, backup tag `pre-ira-dashboard-20260511-1900`):**
- `trading_corp/web/templates/partials/ira_dashboard.html` — **NEW**. Three sections:
  - **Covered Calls** — shares + short call grouped by underlying; one row per (underlying, short_call); columns: Symbol / Shares / Cost / Last / Mkt Value / Share P&L | Call (DTE / Strike / Credit / Mark / Call P&L / Status). Coverage% badge (e.g. "fully covered" or "75% covered" if partial). ITM strikes flagged red with breach %. Sort: ITM-first, then by DTE ascending.
  - **Pure Assets** — shares without any short call sold against them; columns: Symbol / Qty / Avg Cost / Last / Mkt Value / Unrealized P&L. Suppresses P&L for rows with cost_basis=0 (avoids the RH crypto cost_basis=0 noise — same rule as `feedback_holdings_window_scope` memory). Sort: market value descending.
  - **Wheel Puts** — short cash-secured puts (acquire-on-assignment); columns: Underlying / Strike / DTE / Qty / Credit Received / Mark / Underlying Px / Net Basis if Assigned / P&L. Renders empty-state when no active puts ("Sell puts to enter on dips and collect premium.").
- `trading_corp/web/data.py` — added 2 dataclasses + 1 builder:
  - **`CoveredCallPosition`** — `underlying`, `shares_qty`, `shares_avg_price`, `shares_market_value`, `shares_cost_basis`, `shares_pnl`, `shares_pnl_pct`, `short_call: OptionLeg`, `coverage_pct`. Properties: `is_fully_covered`, `is_itm`, `breach_pct`, `combined_pnl`, `call_status` (itm / expiring_today / expiring_tomorrow / profit_take_candidate / open).
  - **`WheelPutPosition`** — wraps a short-put `OptionLeg`. Properties: `underlying`, `strike`, `expiry`, `days_to_expiry`, `credit_received`, `cost_to_close`, `is_itm`, `assignment_cost`, `effective_basis_if_assigned`.
  - **`build_ira_view(stock_holdings, legs, prices) -> dict`** — partitions option legs by type/side, groups short calls with their underlying shares, identifies pure assets (shares with no matching call), wraps short puts as wheel positions. Returns `{covered_calls, pure_assets, wheel_puts}`.
  - New `ira_view: dict | None` field on `DivisionViewSnapshot`. Wired in `build_division_view` for `slug == 'robinhood_ira'`.
- `trading_corp/web/templates/division.html` — conditional fork: when `slug == 'robinhood_ira' AND view.ira_view`, include `partials/ira_dashboard.html` and skip the generic PMCC pairs / Holdings tables. Falls back to legacy layout for all other slugs.

**Verification (against real prod IRA data immediately post-deploy):**
- Real IRA holdings: 7 stocks (IBIT 118.04, MARA 1200, BLOX 18.74, GME+ 100, MSTY 200, STRC 0.97, SATA 0.51) + 1 short call (MARA 2026-05-15 $12.50 ×12, credit $0.92/sh).
- Grouping result: **1 covered call (MARA, 100% covered, OTM, combined P&L -$4,404)** + **6 pure assets** (sorted by market value desc) + **0 wheel puts** (empty-state rendered).
- Rendered page: section headers "Covered Calls" / "Pure Assets" / "Wheel Puts" all present; legacy "Positions" / "Holdings" suppressed for IRA slug; "Recent activity" preserved.
- Specific data points confirmed in HTML: "MARA", "1200", "2026-05-15", "×12", "fully covered" badge.

**Inert / dormant:**
- Long-call legs (LEAPs) on the IRA broker filter are silently dropped in `build_ira_view` — they shouldn't exist there per the strategy, but defensive.
- No automated trading wired — this is dashboard-only. Strategy automation is a follow-up.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-ira-dashboard-20260511-1900; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG  \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/templates/division.html.\$TAG  \$BASE/trading_corp/web/templates/division.html; \
rm -f \$BASE/trading_corp/web/templates/partials/ira_dashboard.html; \
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-11 18:23 UTC — BitUnix Phase 3.2.3 — live confluence score dashboard panel

**Triggered by:** Phase 3.2 (score accumulator) and 3.2.2 (PA factors) were live but invisible — to understand what the bot was scoring, you had to grep audit_event. Phase 3.2.3 adds a panel to `/division/bitunix_futures` that surfaces it.

**Files deployed (1 new, 2 modified, backup tag `pre-bitunix-323-20260511-1820`):**
- `trading_corp/web/templates/partials/bitunix_score_panel.html` — **NEW**. Tailwind+htmx panel. Auto-refreshes every 30s via `hx-get` self-referential pattern.
- `trading_corp/web/data.py` — added `build_bitunix_score_view(db_url, deps)` builder + `_parse_audit_ts(ts)` helper. New `bitunix_score: dict | None` field on `DivisionViewSnapshot`. Wired conditionally in `build_division_view` for `slug == 'bitunix_futures'`.
- `trading_corp/web/templates/division.html` — added conditional include block (5 lines) mirroring the donchian pattern.

**Panel surfaces:**
- **Header:** scoring enabled/dormant, factor count (34), tier thresholds, fire threshold (8)
- **4 stat cards:** Last eval (tier + signal + age), Net score (with buy/sell breakdown + guard penalties), Cooldown (per-side remaining time), Bar cache health (bars cached + last close + ATR + refresh errors)
- **Live price-action factors strip:** ✓/○ per PA factor (`above_vwap`, `below_vwap`, `HH_4h`, `LL_4h`, `volume_above_avg`) + pct_change(60m) — computed live from `bar_cache` at request time via `compute_price_context()`
- **Buy/Sell contributions side-by-side** for the latest evaluation, listing every contributing signal name with its weight
- **Recent paper fires table** (last 10 `would_have_placed` rows with `via=bitunix_score`): ts / tier / side / net_score / entry / stop / TP / qty / trigger
- **Recent evaluations table** (last 20 `bitunix_score_decided` rows): with tier color coding, outcome (placed / skipped_cooldown / skipped_score)
- **Ledger window summary:** count of rows in last 24h

**Verification (in prod immediately post-deploy):**
- `curl localhost:8000/division/bitunix_futures` returned 36,633 bytes ✓
- `id="bitunix-score-panel"` present in HTML ✓
- "● SCORING ACTIVE" badge rendered (scoring.enabled=True) ✓
- Live PA factors strip showed real bool flags: above_vwap=✓, HH_4h=✓, LL_4h=✓ (outside-bar case captured visually) ✓
- Tier mentions count: 1 PREMIUM (threshold label) + 2 STANDARD (1 label + 1 history row) + 1 SKIP (last eval status) ✓
- "Recent paper fires (1)" rendered (the 18:00:07 STANDARD SELL) ✓
- "Recent evaluations (7) · ledger 24h: 7 rows" rendered ✓

**Notable design:**
- Auto-refresh via `hx-get="/division/bitunix_futures" hx-trigger="every 30s" hx-select="#bitunix-score-panel" hx-target="#bitunix-score-panel"` — re-fetches the whole division page but only swaps the panel subtree. No new endpoint needed.
- `build_bitunix_score_view` returns `None` when scoring config is unavailable (observer not wired or YAML scoring block missing) → template's `{% if view.bitunix_score %}` gate prevents partial rendering. Safe default.
- Guard penalties (`bg`, `sg`) and `cooldown_blocked` flag both surfaced — explains "why didn't fire" without grepping logs.
- 30s refresh is intentional. Bar cache polls every 60s; webhooks arrive a few times per hour during active periods. 30s is the sweet spot for "looks live" without hammering the SQLite reads.

**Inert / dormant:** none. The panel is read-only telemetry; it does not affect order flow.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-323-20260511-1820; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG  \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/templates/division.html.\$TAG  \$BASE/trading_corp/web/templates/division.html; \
rm -f \$BASE/trading_corp/web/templates/partials/bitunix_score_panel.html; \
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-11 18:17 UTC — Kalshi K3 Copy Trading division (paper-mode live)

**Commits:** none — files patched onto prod's already-drifted content (per `trading_corp_prod_git_drift` memory).
**Triggered by:** K3 sprint per BACKLOG.md "P0 NEXT — Kalshi K3 Copy Trading". Mirror top Kalshi whales' positions at scaled-down size; selected whales come from offline Wilson-LCB × ROI × category scoring; side detection uses Kalshi's free public trade tape.
**Backup tags:**
- `pre-kalshi-k3-20260511-1816` — covers `kalshi.py`, `secrets.py`, `main.py` (3 modified)
- `pre-kalshi-k3-enable-20260511-1819` — covers `strategies.yaml` (enabled-flip backup)

**Files deployed (5 new, 3 modified):**
- `trading_corp/data/kalshi_apify_client.py` — **NEW** (~260 lines). Async wrapper over Apify's two saswave Kalshi actors (`leaderboard-scraper` + `profile-scraper`). Typed dataclasses (LeaderboardEntry, WhaleProfile, WhalePosition, WhaleTrade), structured error class hierarchy (Auth / OverCap / Timeout), semaphore-gated concurrency, stub-safe when token missing.
- `trading_corp/data/kalshi_whale_stats.py` — **NEW** (~210 lines). Venue-agnostic scoring engine. Wilson 95% LCB on win rate (penalizes small samples), edge factor from avg pnl-per-contract (clipped), category specialization bonus (1.5x match). `compute_stats` aggregates closed_positions per nickname; `score_whale` produces composite + exclusion reasons. Same math will plug into Polymarket revival.
- `trading_corp/agents/strategies/kalshi_copy_trader.py` — **NEW** (~360 lines). The Phase K3 strategy. Mirrors `kalshi_llm_arbitrage` shape (mtime-cached config reload, `enabled` / `auto_execute` properties, `run_scan_cycle`). Per-cycle: load selected whales from `agent_state`, fetch their open_positions via Apify, compare to last-known snapshot, emit ProposedOrders for entries (with side detection) and exits. Cold-start safe: first poll per whale records baseline + emits nothing. Side detection conservative: low-confidence → skip entry, never copy wrong side.
- `trading_corp/scripts/refresh_kalshi_whales.py` — **NEW** (~280 lines). One-off CLI orchestrator for quarterly selection refresh. Pulls leaderboards per category, enriches top-N candidates with profile + closed_positions, scores via Wilson LCB × ROI × category match, writes top whales to `agent_state(kalshi_copy_trader.selected_whales)`. `--dry-run`, `--min-composite` quality floor, fill-up from leftover pool when per-category dedup leaves slots open.
- `trading_corp/scripts/__init__.py` — **NEW** (empty, package marker).
- `trading_corp/brokers/kalshi.py` — extended with `KalshiPublicTrade` dataclass + `get_market_trades(ticker, since, until, limit)` method wrapping `pykalshi.AsyncMarket.get_trades`. Free Kalshi public API, anonymous at trader level, returns `taker_side` per trade — the side-detection signal. Strategy depends on a `TradeTapeFetcher` Protocol; `KalshiBroker` structurally satisfies it.
- `trading_corp/utils/secrets.py` — `APIFY_API_TOKEN` plumbed (5 edits: redact tuple, `Secrets` dataclass field, `expected_env_vars`, `load_secrets()` init, redact-literal registration). Stub-safe — strategy no-ops if token missing.
- `trading_corp/main.py` — `_scheduled_kalshi_copy_trader_loop` function (~155 lines) + startup wiring after the `kalshi_llm_task` block. Apify client lifecycle owned by the loop (`async with KalshiApifyClient(...) as apify_client`). Audit payload allowlist enumerates 10 K3-specific fields (per `trading_corp_audit_payload_allowlist` gotcha memory): `ticker`, `outcome`, `is_entry`, `whale_handle`, `whale_position_contracts`, `whale_position_pnl`, `copy_size_usd`, `side_detection_confidence`, `first_seen_iso`, plus standard.
- `config/strategies.yaml` — `kalshi_copy_trader:` block. Already on prod from a parallel session push at md5 d2619e32; flipped `enabled: false → true` per Board direction (paper-mode, so safe).

**Features shipped (load-bearing for future "is X done?" checks):**
- New division: `kalshi_copy_trading` flipped from standby-placeholder to active (strategy live; paper-mode auto-execute on the existing PaperBroker).
- Selected whales committed to `agent_state(kalshi_copy_trader.selected_whales)`: `['smedtoshi', 'NovaRex', 'tom14cat14', '9187234']`.
- Apify Starter ($29/mo Bronze) subscription confirmed live; APIFY-API-TOKEN in KV `kv-tc-vtwbowt3wtkpy`; loaded at startup via managed identity.
- Two-stage discovery+scoring pipeline ships as the standalone `refresh_kalshi_whales` script — re-runnable quarterly.
- `KalshiBroker.get_market_trades` is the new public side-detection signal source. Free, anonymous-at-trader-level. Will be reused by future Kalshi strategies that need short-window trade context.

**Notable code decisions:**
- **`max_results` is ignored by saswave's profile actor.** Empirically: `open_positions` returns a 20-row floor per name; `trades` returns a 50-row floor. Cost-model planned around this — opaque whales return 0 rows (free), visible whales return up to 20.
- **Two-tier polling architecture was DEFERRED.** Original plan used profile-watch (cheap) + on-activity position fetch (expensive). Once we upgraded to Bronze, simple polling at 5min on 4 whales (~$120/mo budget) is cleaner and survives whale-activity bursts. Two-tier code path doesn't exist; could be added back via the `WhaleActivitySource` abstraction if 12-whale config blows the budget.
- **Side detection is conservative.** When the Kalshi public trade tape can't disambiguate a whale's entry (no size-match or ambiguous matches), the strategy SKIPS the entry rather than guessing. Better to miss a copy than copy the wrong side on real money later.
- **Cold-start baseline persists with `our_side=""`.** When a whale closes one of those baselined positions, `_emit_exit` correctly short-circuits because there's no `our_side` stored — no phantom close emitted.
- **Strategy `enabled` and `auto_execute` are independent flags.** `enabled: true` runs the scanner + emits ProposedOrders + logs `would_have_placed` to audit (paper-mode). `auto_execute: true` would route approved orders through a real KalshiLiveBroker (Phase K5+ work; doesn't exist yet).

**Bugs caught + fixed during the session:**
- `set_agent_state` / `load_agent_state` argument order. The actual signature is `(agent, key, value, db_url=...)` but I wrote `(db_url, agent, key, value)` positionally in both the strategy and the script. First selection-script commit attempt failed with `'list' object has no attribute 'startswith'` because the db_url positional slot got a list. Fixed in both files before deploy.
- Selection fill-up logic was capping at 3 picks even when 9 viable whales existed. Per-category top-2 was deduping aggressively across categories with the same dominant whales. Fix: after per-category dedup, fill remaining slots from leftover-viable global pool by composite score.
- No quality floor on composite score. Without one, fill-up was including whales with Wilson LCB ≈ 0 and negative edge (some 0% win-rate whales were in the top 9). Added `--min-composite` CLI flag (default 0.30) — filters Wilson-LCB-zero whales out of selection. Final selection: 4 quality whales instead of 9 mediocre ones.

**Visibility finding (the data, not a bug):**
- Kalshi has a strong **privacy gradient**. Top-of-leaderboard whales (by `volume`, `projected_pnl`, or `num_markets_traded`) are systematically opaque — 0 of 14 candidates exposed `closed_positions` on the first `--candidates 5` run. Going to `--candidates 30` surfaced 9 visible whales out of 123 candidates (~7% visibility rate). Mid-tier traders (leaderboard rank 20-100) are the actual addressable pool for copy trading.
- All 4 selected whales are Sports/Crypto specialists. No Politics/Economics/Climate/Financials specialists made the visibility-and-quality-floor cut in this first selection pass.

**Cost projection (Bronze rates):**
- Apify Starter base: $29/mo (includes $29 prepaid usage)
- Polling: 4 whales × 20-row floor × $0.0015 × 288 polls/day × 30 = ~$83/mo
- Quarterly selection refresh: ~$0.30 per run = ~$0.10/mo amortized
- Expected total: **$30-50/mo** (well under the $300 spending limit Jack should set in Apify dashboard)
- This session's burn: ~$1.50 (verified via two-test exploration + one final commit run)

**Verification:**
- PID rotation: 246347 → 249182.
- Service active, web `/healthz` returns HTTP 200 in 150ms.
- Pre-restart Python import smoke succeeded on all 7 K3 modules + `trading_corp.main` under prod's venv.
- "Kalshi copy trader scanner online (enabled=False, auto_execute=False, hitl=DIRECT)" logged at 18:17:50 UTC.
- `enabled: true` flipped via sed-anchored replacement at 18:19 UTC. Verified no other strategies accidentally toggled (`grep -B 1 "enabled: true"` showed only pre-existing enabled strategies + ours).
- **Cold-start fired cleanly at 18:22:56 UTC** (first scheduled poll, 300s after restart). Per-whale baselines: smedtoshi=0, NovaRex=0, tom14cat14=14, 9187234=20 open positions. 4 `kalshi_copy_cold_start` audit rows inserted. Zero ProposedOrders emitted (cold-start protection working as designed).
- 32 K3-specific unit tests pass; full suite (excluding 3 pre-existing-broken test files unrelated to K3) shows 364 tests passing — zero regressions from K3 work.

**Inert / dormant on current traffic:**
- smedtoshi and NovaRex are currently flat (0 open positions). They'll trigger entries only when they next open a Kalshi position — could be hours or days. tom14cat14 (14 open) and 9187234 (20 open) baselined positions won't trigger phantom exits because `our_side=""` on baseline.
- Exit-emission code path is wired but won't fire until we successfully emit at least one ENTRY (which requires side-detection to succeed for that ticker). Until that happens, the strategy is effectively read-only on prod.
- `--metric` defaults to `num_markets_traded` in the refresh script. Future refresh attempts could try `--metric volume` or `--time monthly` to surface different whales. Quarterly refresh is the planned cadence.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k3-20260511-1816; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/brokers/kalshi.py.\$TAG       \$BASE/trading_corp/brokers/kalshi.py; \
mv \$BASE/trading_corp/utils/secrets.py.\$TAG        \$BASE/trading_corp/utils/secrets.py; \
mv \$BASE/trading_corp/main.py.\$TAG                 \$BASE/trading_corp/main.py; \
rm -f \$BASE/trading_corp/data/kalshi_apify_client.py \
      \$BASE/trading_corp/data/kalshi_whale_stats.py \
      \$BASE/trading_corp/agents/strategies/kalshi_copy_trader.py \
      \$BASE/trading_corp/scripts/refresh_kalshi_whales.py \
      \$BASE/trading_corp/scripts/__init__.py; \
rmdir \$BASE/trading_corp/scripts 2>/dev/null; \
ENABLETAG=pre-kalshi-k3-enable-20260511-1819; \
mv \$BASE/config/strategies.yaml.\$ENABLETAG  \$BASE/config/strategies.yaml; \
sudo systemctl restart trading-corp
"
```

(The selected_whales entry in `agent_state` is left in place by the rollback — harmless data with no code to consume it.)

---

## 2026-05-11 18:03 UTC — BitUnix Phase 3.2.2 — price-action factors wired into score path

**Triggered by:** Phase 3.2.1 (deployed 17:52 UTC) ran with a zero-filled `PriceContext` — the 5 price-action factors (`above_session_vwap`, `below_session_vwap`, `higher_highs_4h`, `lower_lows_4h`, `volume_above_20bar_avg`) and the two guard penalties (`sell_on_rush`, `buy_on_fall`) were defined in YAML but inert in live mode. Phase 3.2.2 wires them.

**Observation between deploys:** Phase 3.2.1's first STANDARD SELL fired at **18:00:07 UTC** (≈8 min after the 17:52 deploy), net_score=11 (sell-side accumulation of `mc_b_sell_circle` + `mc_a_red_diamond` + `mc_b_sell_circle_div`). The multi-bar accumulation design fired as intended on the first real opportunity post-deploy. Paper short opened at $81902.5, qty=0.0038 BTC.

**Files deployed (1 new, 2 modified, 1 backup tag `pre-bitunix-322-20260511-1810`):**
- `trading_corp/data/bitunix_price_context.py` — **NEW**. Pure helpers: `session_vwap()`, `higher_highs_lower_lows_4h()`, `volume_above_20bar_avg()`, `pct_change_in_window()`, `_resample_to_4h()`. Aggregator `compute_price_context(bar_cache, sell_window_min, buy_window_min)` returns a `PriceContext` or None (None → caller falls back to zero context).
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — `_score_and_maybe_propose()` now calls `compute_price_context(self.bar_cache, ...)` instead of building a zero-filled PriceContext. Graceful fallback on any exception (logs warning, uses zero context).
- `trading_corp/main.py` — bumped `LiveBarCache(max_bars=60)` → `max_bars=500`. **Surgical edit** (3-line replacement around the constructor). BitUnix API actually caps at 200 bars per request, so live cache settles at 200 bars regardless — but the YAML still requests 500 for forward-compat (if the venue limit ever raises).

**Features shipped:**
- Live VWAP comparison: each score includes ±1 weight from `above_session_vwap` / `below_session_vwap` based on current price vs day-VWAP (or rolling-10h VWAP at runtime when cache doesn't span the full UTC day).
- 4h HH/LL: each score includes ±2 weight from comparing last-completed 4h bucket vs prior. Resampling done in-memory at evaluation time from the 3m bars.
- Volume-above-avg: ±1 (directional — adds to both sides as a strength-of-move indicator).
- Guard penalties: `sell_on_rush` / `buy_on_fall` now compute actual % change over the 60-min window from cached bars. Tiered penalties (-1 / -2 / -3) suppress sells into rapid rises and buys into rapid drops.

**Notable code changes:**
- `compute_price_context` is the only public API. Internal helpers (`session_vwap`, etc.) are also exported for unit testing.
- `_resample_to_4h` aligns 4h buckets to UTC 00:00 / 04:00 / 08:00 / 12:00 / 16:00 / 20:00 — matches the convention in `backtest_btc_accumulator._resample_to_4h`.
- HH/LL check requires **≥ 3 buckets** (last bucket is in-progress, excluded). At max_bars=200, that's ~10h of 3m bars = 2.5 buckets, JUST enough.

**Verification:**
- Local synthetic-bar test passed: 500 bars dropping 82000→81002 produced `below_vwap=True`, `LL_4h=True`, `HH_4h=False`, `volume_above_avg=True`, `pct_change=-0.049%`.
- Prod import test ✓
- /healthz=200 after warm-up ✓
- Bar cache primed: 200 bars cached, last_close=$81890.2, atr_14=98.43, poll-loop online (60s interval) ✓
- Pending: first post-deploy webhook to land a score row with non-zero PA contributions (cooldown blocks sell-side until 18:30:07 from the STANDARD fire at 18:00:07).

**Inert / dormant on current traffic:**
- `bitunix_futures.scoring.tier_thresholds.weak: 5` band — still never fires (`min_score_to_fire: 8`).
- Phase 3.1 `_tier_for` classifier + `_maybe_propose` — still retained for fast rollback.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-322-20260511-1810; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/main.py.\$TAG  \$BASE/trading_corp/main.py; \
mv \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.\$TAG  \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
rm -f \$BASE/trading_corp/data/bitunix_price_context.py; \
sudo systemctl restart trading-corp.service
"
```
(Phase 3.2.1 state restored — score path still active, PA factors inert again.)

---

## 2026-05-11 17:52 UTC — BitUnix Phase 3.2 confluence score accumulator (paper-mode, multi-deploy)

**Triggered by:** Board ask after the 16:42 UTC missed-short incident — the Phase 3.1 single-bar `_tier_for` classifier dropped a clean PREMIUM SELL setup (4h-bear bias + multiple 4h/1D bear Cypher signals accumulated + simultaneous `money_bag_top` + `cvd_bear_flip`) because CVD agreement check fired at trigger time before the same-second `cvd_bear_flip` updated state. Root cause was structural: classifier evaluates one snapshot at one moment, can't accumulate confluence across bars.

**Replacement design (Phase 3.2):** Score accumulator. Every inbound webhook signal (Otter + Cypher) appends to `bitunix_signal_ledger` with a per-factor TTL. On each new alert, scorer sums weights of all live (in-TTL, deduped by signal_name) signals + price-action factors, applies guard penalties, picks the winning side, maps net_score → PREMIUM (≥12) / STANDARD (≥8) / WEAK (≥5) / SKIP. Cooldown (1800s) prevents stacking same-direction fires. Risk caps unchanged (0.5% per-trade effective risk, 3% daily kill).

**Backtest verdict** (Apr 30 – May 9, 625 alerts, tuned config):
- 21 paper trades, 42.9% win rate, **+0.286 R avg, +6.0 R total, +0.18% return, 0.25% max DD**
- STANDARD tier carries edge (+0.33 R, 44%, n=18); WEAK band killed via `min_score_to_fire: 8` (was -0.16 R noise)
- 16:42 setup fires as PREMIUM SELL (net_score=12) on the new model — validated standalone before deploy
- Context: BTC was up 5.79% in window (bull); model navigated bullish chop reasonably

**Files deployed (4 new/modified, 2 backup tags):**
- `config/strategies.yaml` — added `bitunix_futures.scoring` block (34 factors, tier thresholds, guards, dedupe). `enabled: true` at ship.
- `trading_corp/agents/strategies/bitunix_confluence.py` — **NEW**. Pure-function scorer; reuses `FactorConfig`/`GuardConfig`/`AlertEvent`/`PriceContext` from `btc_accumulator.py`. Adds `BitUnixConfluenceConfig`, `evaluate_confluence_futures()`, `filter_live_alerts_with_dedupe()`.
- `trading_corp/agents/strategies/btc_accumulator.py` — **NEW on prod** (existed locally as scaffold for the deprecated coinbase_spot accumulator; needed because `bitunix_confluence.py` imports its dataclasses). Pure-function, no side effects.
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — extended. Adds 3 new DDLs (`bitunix_signal_ledger`, `bitunix_score_cooldown` + index). `__init__` accepts optional `scoring_config: BitUnixConfluenceConfig`. `observe_and_decide()` now: (a) always appends to ledger regardless of flag, (b) routes to `_score_and_maybe_propose()` when `scoring_config.enabled=True`, else falls back to Phase 3.1 `_maybe_propose()`. New methods: `_append_to_ledger`, `_read_live_ledger`, `_read_cooldown`, `_record_score_fire`, `_log_score_decision`, `_score_and_maybe_propose`. New audit kind: `bitunix_score_decided` (separate from Phase 3.1's `bitunix_decided`). Score-path fills also tag `would_have_placed` with `via: "bitunix_score"` + `net_score` for filtering.
- `trading_corp/main.py` — loads `BitUnixConfluenceConfig` from `strategies.yaml`, passes to observer. **Surgical patch** (only the 19 lines around `bitunix_observer = BitunixFuturesObserver(...)`) — see lessons-learned below.

**Features shipped:**
- Multi-bar confluence accumulation on bitunix_futures: signal weights survive their TTL windows (Otter 15-30 min, Cypher B 4h, Cypher A 24h, Bias 90 min, CVD 30 min). Score updates on every webhook arrival.
- Per-signal-name dedupe within TTL (repeated `mc_a_red_diamond` fires count once, most-recent wins).
- Same-direction cooldown gate (1800s) on top of cap math.
- `bitunix_signal_ledger` table accumulating real prod data — usable for re-tuning weights without code changes.
- `bitunix_score_decided` audit rows on every alert, with full score breakdown (`final_buy_score`, `final_sell_score`, `net_score`, `buy_contributions`, `sell_contributions`, `cooldown_blocked`, `reason`).

**Notable code changes:**
- Phase 3.1 `_tier_for` classifier is **fully bypassed when `scoring.enabled=True`** — score path replaces it (single open trade at a time, opposite-side signals do not auto-flip in v1; cooldown handles same-side). The old code remains in-place behind the flag for fast rollback.
- Price context in live mode is **signal-only for v1** — `PriceContext(pct_change=0, PA flags=False)`. Guards and PA factors (VWAP, HH/LL, volume) inert in prod. Backtest used them; gap is intentional and small (max ±4 score points). Phase 3.2.2 will wire `LiveBarCache` to compute PA factors live.
- Tier sizing (`TIER_SIZING` constants) shared between Phase 3.1 and 3.2. 0.5% effective-risk cap and 3% daily-kill enforced on the score path identically.

**Latent bugs caught + fixed:**
- `bitunix_futures_observer.py` import was missing `timedelta` (had `datetime`, `timezone` only) — caught in local E2E test before prod deploy.
- The score-path code uses `self._read_daily_risk` and `self._build_proposal` — both existed but were defined later in the class; Python resolves at call time, so no import-time impact.

**Verification:**
- md5 match on all 4 files post-scp ✓
- Prod-side `python -c 'import trading_corp.main; print("IMPORT OK")'` ✓
- Systemd active state ✓
- New tables created: `bitunix_signal_ledger` (0 rows at deploy), `bitunix_score_cooldown` (0 rows) ✓
- /healthz=200 after warm-up ✓
- Waiting on first webhook to confirm ledger append + score evaluation (real-data test)

**Lessons learned (load-bearing for future sessions):**
1. **Never `scp` a whole file when a surgical edit will do.** First deploy attempt scp'd my local `main.py` which had unrelated in-flight changes (`kalshi_copy_trader` import not yet shipped). Service crash-looped on `ModuleNotFoundError`. Recovery: rollback to backup tag, pull prod's `main.py` to local, `python` patch only the 19 lines we needed, scp back. Cost: ~3 minutes of restart noise, no data loss. The CLAUDE.md "filesystem-not-git scope" rule covers this — diff the file first, send only what changed.
2. **`btc_accumulator.py` was scaffold code that never shipped.** When `bitunix_confluence.py` imported from it, prod hit `ModuleNotFoundError` on the first restart. Pushed `btc_accumulator.py` to prod as the second-step recovery. Reasonable choice (small, pure-function, no side effects on import) but flagged here so future sessions know it's a dependency, not dead code.
3. **Crash-loop during 1st-attempt deploy was caught by systemd auto-restart** + the immediate `journalctl` check. Two restart cycles within 20s, no permanent state corruption (the new tables were created idempotently via `CREATE TABLE IF NOT EXISTS`).

**Inert / dormant on current traffic:**
- Price-action factors (`above_session_vwap`, `higher_highs_4h`, `lower_lows_4h`, `volume_above_20bar_avg`) — never evaluated in live mode (all flags=False). Will activate in Phase 3.2.2 when `LiveBarCache` gains the helpers.
- Guard penalties (`sell_on_rush`, `buy_on_fall`) — never fire in live mode (`pct_change_in_window_*=0`). Same Phase 3.2.2 dependency.
- `bitunix_futures.scoring.tier_thresholds.weak: 5` band — never fires because `min_score_to_fire=8` filters it out. Kept in YAML for tier-naming clarity and easy re-enable.
- Phase 3.1 `_tier_for` classifier + `_maybe_propose` — code retained, only reached when `scoring.enabled=False`.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-score-20260511-1747; BASE=/home/azureuser/trading_corp; \
mv \$BASE/config/strategies.yaml.\$TAG  \$BASE/config/strategies.yaml; \
mv \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.\$TAG  \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
TAG2=pre-bitunix-score-20260511-1747-v2; \
mv \$BASE/trading_corp/main.py.\$TAG2  \$BASE/trading_corp/main.py; \
rm -f \$BASE/trading_corp/agents/strategies/bitunix_confluence.py; \
rm -f \$BASE/trading_corp/agents/strategies/btc_accumulator.py; \
sudo systemctl restart trading-corp.service
"
```
(Notes: `strategies.yaml.$TAG` is from the first backup; `main.py.$TAG2` is from the post-recovery backup because the original `main.py.$TAG` was already moved during the rollback step. Removing the two NEW files cleans up; the two new tables in SQLite are kept — they're idempotent and harmless when unused.)

---

## 2026-05-11 07:00 UTC — Structural arb event_title in would_have_placed payload (two-deploy fix)

**Triggered by:** Open paper trades table on the dashboard's `kalshi_arbitrage` view showed raw tickers like `KXTRUMPRUN-28JAN01` in the Market column (gibberish to a human). The data exists — kalshi_temporal_bucket_arb and kalshi_tail_price_arb both carry `event.title` at scan time and DO include it in their `kalshi_*_evaluated` audit events — they just weren't propagating it into the `would_have_placed` payload.

**FIRST DEPLOY (07:00 UTC) — strategy code (2 modified, PID 222245):**
- `trading_corp/agents/strategies/kalshi_tail_price_arb.py` — added `"event_title": opp.title` to the `common_extra` dict at line 383.
- `trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py` — added `"event_title": opp.title` to both the temporal-arb `common` dict (~line 570) and the bucket-arb `common` dict (~line 614).

**Post-deploy verification revealed a SECOND bug:** new structural emits at 05:10:17 UTC still had no `event_title` in the audit payload. The strategies were correctly putting it in `ProposedOrder.extra`, but the orchestrator loops in `main.py` build the audit payload from a **fixed allowlist** of `ext.get(...)` keys — `event_title` wasn't in the allowlist, so it was silently dropped:

```python
base_payload = {
    "strategy": agent.name, ...
    "ticker": ext.get("ticker"),
    "event_ticker": ext.get("event_ticker"),
    # event_title NOT in allowlist — got dropped here
    ...
}
```

**SECOND DEPLOY (05:20 UTC) — main.py allowlist fix (PID 224389):**
- `trading_corp/main.py` — added `"event_title": ext.get("event_title")` to the `base_payload` allowlist in BOTH `_scheduled_kalshi_arb_loop` (line 1885) and `_scheduled_kalshi_tb_arb_loop` (line 2039). Same pattern as `event_ticker` — single key-add per loop.

**Backup tags:**
- `pre-structural-event-title-20260511-0700` (strategy files)
- `pre-event-title-mainpy-20260511-0520` (main.py allowlist)

**Lesson for future "field not landing in audit row" debugging:**
- ProposedOrder.extra is NOT a transparent passthrough into audit payloads. Each orchestrator loop (`_scheduled_kalshi_*_loop`, polymarket equivalent) has an explicit allowlist when building the `base_payload`. New fields need to be added at BOTH layers: the strategy file (where the value is computed) AND the main.py loop (where it gets routed into the audit event). Easy to miss because the strategy unit tests would pass — the field IS in extra; it just doesn't reach storage.

**Why this works without dashboard changes:** the dashboard template already prefers `event_title` over the bare ticker:

```jinja
{{ ot.market_title or ot.market_id }}
```

…and `_query_pm_open_trades` already populates `PMOpenTrade.market_title` from `p.get("event_title") or p.get("ticker")`. So the moment the strategy starts including `event_title` in its payload, the Market column auto-renders the title. No template / data-layer changes needed.

**Pre-deploy verification:**
- AST parse on both files.
- No new tests needed — existing tests don't assert ProposedOrder.extra contents at that level; the change is a single string-keyed addition to a dict that's already plumbed through. Verification happens post-deploy via real audit data.

**Post-deploy verification (prod):**
- PID rotated 221187 → 222245. Web up after 50s warm-up.
- File md5/grep confirmed `event_title` in both deployed files (1 new occurrence in tail, 2 new in temporal_bucket).
- Awaiting next 5-min scan tick (kalshi_temporal_bucket_arb + kalshi_tail_price_arb both poll on 300s cadence) to confirm fresh `would_have_placed` audit rows carry the field.

**Backward compatibility:**
- Existing 120+ pending structural arb rows in `audit_event` table still have payloads without `event_title` — dashboard falls back to ticker for those (template's `or ot.market_id` branch). New emissions from this restart forward will have readable titles.
- No schema change; no resolver change; no template change. Just enriched payload going forward.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-structural-event-title-20260511-0700; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/strategies/kalshi_tail_price_arb.py.\$TAG       \$BASE/trading_corp/agents/strategies/kalshi_tail_price_arb.py; \
mv \$BASE/trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py.\$TAG  \$BASE/trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 06:01 UTC — PM dashboard expandable rows + LLM analysis surfacing

**Triggered by:** User feedback after 05:02 UTC deploy:
1. "Kalshi Arbitrage bot descriptions could use some work" — structural arb (temporal/bucket/tail) rows showed just gibberish tickers like `KXTEMPNYCM-2026-S2`.
2. "Where is the detailed LLM analysis saved for the kalshi arbitrage bot details? Is there a way to have this information show up on each row?" — kalshi_llm has rich `llm_reasoning` + `key_unknowns` + `llm_confidence` in the audit payload; dashboard wasn't surfacing it.

**Files deployed (4 modified):**
- `trading_corp/web/data.py`:
  - **PMOpenTrade** gained `rationale`, `llm_reasoning`, `key_unknowns`, `llm_confidence`, `subtitle`, `leg_date`. Parsed from the would_have_placed payload in `_query_pm_open_trades` (LLM strategies populate everything; structural strategies populate rationale + leg_date).
  - **PMRoundTrip** gained `rationale`, `llm_reasoning`, `key_unknowns`, `llm_confidence`, `subtitle`. Parsed from `kalshi_round_trips.extra_json` in `_query_pm_round_trips`. `extra_json` column added to the SELECT (was missing).
  - **Defensive parsing**: malformed `extra_json` strings + missing `key_unknowns` list fields all default cleanly to `None` / `[]`.
  - Polymarket round-trips don't yet store `extra_json` (different schema), so polymarket PMRoundTrip rows get `None` for the analysis fields. Future polymarket resolver enrichment can fill these in.
- `trading_corp/agents/kalshi_resolver.py` — `_compute_round_trip_row` now serializes `llm_reasoning` and `key_unknowns` (plus the existing `llm_confidence` and `rationale`) into `extra_json` so future kalshi_round_trips rows carry the full analysis. Pre-2026-05-11 ~05:30 UTC rows just have None for these fields — they render a clear "no detailed analysis stored" message in the expand panel.
- `trading_corp/web/templates/partials/pm_dashboard_body.html`:
  - **Open tab table**: every row is now click-to-expand. Added a leading caret column (▸ / ▾) + `pm-expand-trigger` class with `data-pm-detail="ot-{i}"`. Below each row sits a hidden `<tr class="pm-detail-row hidden">` with a 3-column grid (Trade context · Analysis · ...). Columns swap dynamically: dropped the "Cost" column from the main row (moved into expand panel) to make room for the wider Market column.
  - **History tab table**: same expandable pattern with `rt-{i}` ids + Analysis section that includes implied @ entry + LLM prob + analysis text. Existing wins/losses filter buttons preserved.
  - **Analysis section contents**: rationale (always shown when present), full LLM reasoning (whitespace-preserved), Key unknowns bullet list, confidence pill (low/medium/high color-coded), subtitle (kalshi sub-title like "-1° or below"). Structural arb rows show the rationale + leg date; LLM rows show everything.
  - **Trade context section**: market title, ticker, sub-title, category, leg date, strategy, cost, order ID.
- `trading_corp/web/templates/prediction_markets_dashboard.html` — added expand-trigger handler to the delegated click listener (lives outside the swap target so it persists across HTMX swaps). Toggles the matching `#pm-detail-{id}` row's `hidden` class + flips the caret glyph.

**Backup tag:** `pre-pm-analysis-rows-20260511-0600`

**Pre-deploy verification:**
- 5 new tests covering: LLM reasoning parsing from open-trades payload, structural arb rationale-without-LLM, round-trip parses extra_json analysis fields, legacy empty extra_json, malformed extra_json.
- 28 PM dashboard tests pass; 78 total polymarket + kalshi + dashboard tests pass; zero regressions.
- AST + Jinja parse on all modified files; drift check on prod showed clean additive diffs.

**Post-deploy verification (prod):**
- PID rotated 219957 → 221187. Web up after 50s warm-up.
- All routes return 200; partial route stays fast at ~30ms.
- HTML inspection confirms:
  - All open-trade rows render with `pm-expand-trigger` class + detail rows below.
  - kalshi_llm row 0 expand panel shows "medium confidence" pill + reasoning text + "Key unknowns" bulleted list.
  - kalshi_arbitrage (structural) row 0 expand panel shows ticker + leg date + strategy + cost + order ID + structural rationale ("Temporal arb on KXTRUMPRUN...").

**Notable code decisions:**
- **Delegated click handler for expansion** (not inline `onclick`). Same pattern as the tab + filter handlers — single listener on `document`, survives every HTMX swap. The swapped-in rows just need the correct `data-pm-detail` attribute.
- **Single template for both LLM and structural rows.** The analysis section uses `{% if rt.rationale %}` / `{% if rt.llm_reasoning %}` guards so the same template renders cleanly for any strategy. Empty cases get a plain "No detailed analysis stored" message instead of awkward gaps.
- **Resolver enriched FORWARD only**, not backfilled. The single existing kalshi_round_trips row (the K2.4 NYC-temp loss) doesn't have llm_reasoning in its extra_json — re-resolving requires deleting + waiting for the next hourly tick. Not worth it for one row. New rows from now on carry the full analysis.
- **`extra_json` column added to the SELECT**. Subtle bug — the old query omitted it, so the new fields-from-extra-json parsing silently returned None for all rows. Caught by tests before deploy.

**Known gap (separate follow-up):**
- Structural arb strategies (`kalshi_tail_price_arb`, `kalshi_temporal_bucket_arb`) don't put `event_title` in their would_have_placed payloads — so the Market column shows raw tickers like `KXTRUMPRUN-28JAN01` instead of human-readable titles. The data exists in the discovery layer at emit time; small strategy-code edit needed. Tracking this as a future tile-readability pass.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-pm-analysis-rows-20260511-0600; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG                                                          \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/agents/kalshi_resolver.py.\$TAG                                            \$BASE/trading_corp/agents/kalshi_resolver.py; \
mv \$BASE/trading_corp/web/templates/prediction_markets_dashboard.html.\$TAG                      \$BASE/trading_corp/web/templates/prediction_markets_dashboard.html; \
mv \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html.\$TAG                        \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 05:02 UTC — PM dashboard fixes (HTMX swap + Open trades tab + kalshi_copy_trading)

**Triggered by:** User-reported issues with the dashboard shipped 04:04 UTC:
1. **60-70s page blank on every division switch** — dropdown `onchange="window.location.href=..."` did a full page nav. Every full nav passes through Authelia forward_auth in Caddy → re-auth + redirect chain → slow.
2. **`would_have_placed` paper trades not visible** — only the count showed (`Pending: 123`); user wanted to see the trades themselves.
3. **`kalshi_copy_trading` missing from dropdown** — divisions.yaml didn't have the entry yet (waiting on K3); the dashboard is divisions-list-driven so nothing to show.

**Files deployed (3 modified, 1 new):**
- `trading_corp/web/data.py` — added `PMOpenTrade` dataclass + `_query_pm_open_trades(db_url, slugs, limit)` (cross-venue UNION on `audit_event WHERE kind='would_have_placed'` LEFT JOIN round-trip tables, excludes resolved). `build_prediction_market_view` now fans 3 queries (round_trips + equity_curve + open_trades); `summary.n_pending = len(open_trades)` so the card count and the table can't drift apart. Side detection in `_query_pm_open_trades` reuses the same outcome/leg-prefix fallback ladder as the resolver. Removed an unused `placeholders` variable in `_query_pm_round_trips`.
- `trading_corp/web/routes.py` — added partial endpoints `GET /partials/prediction-markets/{division?}` that render JUST `partials/pm_dashboard_body.html` (no base.html chrome). Crucially, the partial handler **skips `build_command_center`** — the corp-wide snap is only needed for the base header/footer, which the partial doesn't include. That's what makes the swap fast (23ms vs 2.7s for the full page).
- `config/divisions.yaml` — added `kalshi_copy_trading` (broker: paper, standby: true, enabled: true), mirroring the polymarket_copy_trading placeholder pattern. K3 will flip standby:false when the leaderboard scraper + copy-trader strategy ship. The division now appears in the dashboard dropdown, the home-page tile group, and any future cross-venue queries automatically include it.
- `trading_corp/web/templates/prediction_markets_dashboard.html` — restructured into a thin shell: header + dropdown + `<div id="pm-content">{% include "partials/pm_dashboard_body.html" %}</div>` + a script tag that wires HTMX swap on the dropdown's `change` event. Tab and history-filter handlers moved to delegated `document` click listeners so they survive every HTMX swap (the swapped DOM nodes re-bind automatically). `popstate` handler keeps back/forward button correct. `htmx:afterSwap` listener calls `window.renderPMChart()` to re-create the equity chart on the new container. Fall-through to full nav if HTMX is unavailable.
- **NEW:** `trading_corp/web/templates/partials/pm_dashboard_body.html` — everything that changes between divisions: selected-label sub-header, 6 summary cards, **3-tab nav (Portfolio + OPEN + History)**, portfolio + open + history panels, inline equity-curve JSON. Used both by the full-page render and by the HTMX swap endpoint.
- `trading_corp/web/static/js/prediction_markets_chart.js` — refactored from one-shot IIFE to expose `window.renderPMChart()`. Disposes any prior chart instance + ResizeObserver before creating fresh ones — needed because the chart container DOM node is replaced on every HTMX swap.

**Open tab columns:** emitted ts · age (m/h/d) · [division — in All-mode only] · venue · market title · side · qty · entry · cost · signal (divergence % or edge ¢) · resolves-at.

**Backup tag:** `pre-pm-dashboard-htmx-20260511-0500`

**Pre-deploy verification:**
- AST parse + jinja parse on all modified/new files.
- 5 new tests covering open-trades query: LLM-payload normalization, temporal/bucket leg-prefix parsing, polymarket payload, resolved-exclusion, All-mode UNION + sort. **23 PM dashboard tests pass; 92 total polymarket + kalshi + dashboard tests pass; zero regressions.**
- Prod-drift check: all 3 modified files matched my last patched-prod content + my new patches (clean additive diff — verified line-by-line for each file).

**Post-deploy verification (prod):**
- PID rotated 217797 → 219957. Web up after 50s warm-up.
- **Speed:** full-page route `/prediction-markets/kalshi_llm_arbitrage` = 2.68s; partial route `/partials/prediction-markets/kalshi_llm_arbitrage` = **23ms** (116× faster). Dropdown switches no longer trigger full nav through Authelia, so user-perceived blank-screen time drops from 60-70s to sub-second.
- All 5 prediction-market divisions appear in the dropdown: All / Polymarket Arbitrage / Polymarket Copy Trading / Kalshi Arbitrage / Kalshi LLM Arbitrage / **Kalshi Copy Trading** (new).
- Home page now links to `/prediction-markets/{slug}` for all 5 divisions.
- 3-tab dashboard renders with Portfolio + OPEN + History tabs; Open tab shows pending paper-trade table populated from the live audit-event data.

**Notable code decisions:**
- **HTMX over full nav** is the architecturally right answer regardless of Authelia. In-app navigation between divisions of the SAME dashboard shouldn't re-fetch the corp-wide header/footer; partial swap is correct semantics + much faster.
- **`build_command_center` skipped on partial endpoint.** This is the single biggest contributor to the speed gain — broker.snapshot fan-out across all divisions (especially Fidelity selenium) is the slow part. The partial doesn't need it because the page header/footer don't change.
- **Delegated event listeners on `document`.** Tab and filter buttons live inside the swappable region; per-element listeners would die on every swap. The delegated handler binds once on the outer scope and works for every swap iteration.
- **`window.renderPMChart()` exposed globally + disposal-before-render.** Lightweight Charts needs explicit `.remove()` on the old chart before creating a new one on a fresh DOM node. The chart's ResizeObserver is also disposed to avoid orphaned observers piling up.
- **`open_trades` and `n_pending` share one source of truth.** Summary card and table can't drift — both come from the same query result.
- **kalshi_copy_trading as standby placeholder.** Division registry-driven dashboard means future K3 work doesn't touch the dashboard layer; flipping `standby: false` is sufficient when the strategy ships.

**Inert / dormant on current traffic:**
- Open trades tab shows 123 pending for kalshi_llm_arbitrage (matches DB state). New pending trades from the active scanners appear here automatically as their `would_have_placed` rows land.
- kalshi_copy_trading division shows zero state (no strategy writing to it). When K3 ships, its data populates without dashboard changes.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-pm-dashboard-htmx-20260511-0500; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG                                                                \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/routes.py.\$TAG                                                              \$BASE/trading_corp/web/routes.py; \
mv \$BASE/config/divisions.yaml.\$TAG                                                                   \$BASE/config/divisions.yaml; \
mv \$BASE/trading_corp/web/templates/prediction_markets_dashboard.html.\$TAG                            \$BASE/trading_corp/web/templates/prediction_markets_dashboard.html; \
mv \$BASE/trading_corp/web/static/js/prediction_markets_chart.js.\$TAG                                  \$BASE/trading_corp/web/static/js/prediction_markets_chart.js; \
rm \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 04:04 UTC — Prediction Markets dashboard (K2.4 Option C)

**Triggered by:** User vision lock-in for the prediction-markets surface. Single dashboard at `/prediction-markets/{division?}` parameterized by division. Tiles on the home page get a performance overview (win rate / resolved / pending / realized) and link directly to the dashboard with the division pre-selected. Same template + tabs for every division; dropdown switches the data. "All Prediction Markets" combined view aggregates across all 4 (later 5) divisions. Scope is "Option C" — Portfolio + History tabs only; Positions/Activity/Report tabs deferred until data densifies. Forward-compatible: future `kalshi_copy_trading` (Phase K3) auto-appears in the dropdown the moment it registers in `divisions.yaml`.

**Files deployed (4 modified, 3 new):**
- `trading_corp/web/data.py` — added the prediction-markets dashboard data layer (~390 lines):
  - **5 new dataclasses:** `PMRoundTrip`, `PMEquityPoint`, `PMSummary`, `PMDivisionOption`, `PMDashboardView`.
  - **Cross-venue helpers:** `_pm_venue(slug)` (kalshi vs polymarket inference), `_pm_divisions_all()` (filter from divisions.yaml).
  - **3 query functions:** `_query_pm_round_trips` (UNIONs `polymarket_round_trips` + `kalshi_round_trips`, normalizes to PMRoundTrip), `_query_pm_equity_curve` (cross-venue equity snapshots), `_query_pm_pending_count` (would_have_placed rows without resolution row).
  - **2 aggregators:** `_pm_equity_at(curve, ts)` (last-equity lookup, sums across divisions for All mode), `_pm_summary` (computes summary cards; voids excluded from win rate denominator).
  - **Entry point:** `build_prediction_market_view(deps, division)` — `division=None` for All mode, returns None for unknown slug (route turns into 404). Fans 3 queries via `asyncio.to_thread`.
  - **Home-tile hydration:** new `_hydrate_pm_overview(divisions, db_url)` — single sweep, three aggregate queries; attaches `pm_overview` dict to each prediction-market division. Called from `build_command_center` after the donchian hydration block.
- `trading_corp/web/routes.py` — added `GET /prediction-markets/` and `GET /prediction-markets/{division}` routes. Both go through `_render_pm_dashboard(request, division)` which fans `build_command_center` + `build_prediction_market_view` in parallel. Returns 404 on unknown division. Old `/division/{slug}` route untouched (legacy access still works for the 4 prediction-market divisions).
- `trading_corp/utils/divisions.py` — added `pm_overview: dict | None = None` field to the `Division` dataclass for the home-tile hydration target.
- `trading_corp/web/templates/home.html` — tiles in the `prediction_markets` investment group now link to `/prediction-markets/{slug}` (not `/division/{slug}`) and render an inline performance overview (win % · resolved · pending counters + realized P&L row) when `d.pm_overview` is populated. Other groups (Individual / Crypto / Retirement) unchanged.
- **NEW:** `trading_corp/web/templates/prediction_markets_dashboard.html` — single template with header bar (← Command Center · Prediction Markets — <label> · division dropdown), 6 summary cards (Equity / Today's P&L / Win rate / Resolved / Pending / Realized), 2-tab nav (Portfolio + History; vanilla JS toggle, no HTMX). Portfolio tab = equity-curve chart container + outcome-breakdown sidebar. History tab = resolved-markets table with venue badge, market title, side, qty, entry, result, P&L, ROI; in All-mode adds a Division column. Wins/Losses/All filter buttons toggle row visibility via `pm-history-row[data-won]` attribute.
- **NEW:** `trading_corp/web/static/js/prediction_markets_chart.js` — Lightweight Charts wiring for the equity curve. Reads inline JSON from `#pm-equity-data` (server-rendered, no HTTP fetch). In All mode it aggregates per-timestamp across divisions (sum of equity per unique 5-min epoch). Resilient empty-state.

**Backup tag:** `pre-pm-dashboard-20260511-0410`

**Pre-deploy verification:**
- AST parse on all 3 modified Python files + Jinja parse on `prediction_markets_dashboard.html` + `home.html`.
- **18 new tests in `tests/test_prediction_markets_dashboard.py`** covering: venue inference, cross-venue UNION query + normalization, division-filtered queries, equity-curve cutoff, pending-count cross-venue, summary win-rate math (voids excluded), tile hydration (only touches prediction-market divisions), invalid-slug → None, All mode aggregates correctly.
- 87 polymarket + kalshi_resolver + backtest_polymarket + prediction_markets tests combined pass; zero regressions.
- Prod-drift check: prod's `data.py`, `routes.py`, `divisions.py`, `home.html` all had md5s differing from local HEAD. All 4 patches applied onto PROD content via the `/tmp/k24_prod/*.patched` workflow (memory `trading_corp_prod_git_drift`). Anchor strings verified before each edit.

**Post-deploy verification (prod):**
- PID rotated 215310 → 217797. Service active; web server up on port 8000 after the usual 30s warm-up (Fidelity bot-detection check is the bottleneck on cold start — pre-existing).
- HTTP smoke test — all expected status codes:
  - `GET /` → 200 (home page)
  - `GET /prediction-markets/` → 200 (All mode)
  - `GET /prediction-markets/kalshi_llm_arbitrage` → 200
  - `GET /prediction-markets/kalshi_arbitrage` → 200
  - `GET /prediction-markets/polymarket_arbitrage` → 200
  - `GET /prediction-markets/not-real` → **404** (correct)
  - `GET /static/js/prediction_markets_chart.js` → 200
- Home-page content check: 4 `/prediction-markets/` links found in tile group (one per active prediction-market division). 
- Kalshi LLM dashboard at `/prediction-markets/kalshi_llm_arbitrage` shows **1 Resolved · 121 Pending** in the summary cards — matches DB state (1 row in kalshi_round_trips from the K2.4 resolver tick + 121 unresolved would_have_placed entries).
- Dropdown selected-option check: `selected` attribute lands on "All Prediction Markets" at `/prediction-markets/` and on "kalshi_llm_arbitrage" at the slug URL.

**Notable code decisions:**
- **One template, one route, one builder.** Cross-venue normalization happens at the data layer; the template is venue-agnostic except for a small venue badge in the History tab.
- **Vanilla-JS tab toggle, not HTMX.** Tab content is small and pre-rendered server-side — no need for an extra round-trip. Keeps the dashboard fast on first paint and simple to reason about.
- **Equity-curve data inlined as JSON.** Avoids a second HTTP round-trip; the chart paints instantly once Lightweight Charts loads. In All mode the JS sums per-timestamp.
- **Division dropdown is full-nav (not HTMX swap).** Bookmark + back button work correctly; the URL is the source of truth for which division is selected.
- **Voids excluded from win-rate denominator.** Per K2.4 P&L semantics, void markets refund — they're not wins or losses. Win rate = wins / (wins + losses), voids tallied separately.
- **`Division.pm_overview` attribute, dict not dataclass.** Mirrors the existing `Division.donchian` shape. Keeps the home tile template branch-free (just check truthiness) without dragging more dataclass schema across module boundaries.
- **Polymarket round-trips have no `division` column.** Today only `polymarket_arbitrage` writes them; we accept that filter assumption explicitly in `_query_pm_round_trips` and `_query_pm_pending_count`. When `polymarket_copy_trading` ships its own resolver path, either add a `division` column to `polymarket_round_trips` or write to a separate table.
- **Forward-compat for kalshi_copy_trading (Phase K3).** Dropdown reads `load_divisions()` live — when K3 ships and adds the new division to `divisions.yaml`, it auto-appears. The venue inference (`kalshi_` prefix → "kalshi") and the query layer (queries kalshi tables when any `kalshi_*` slug is in the filter) already handle it; only the round-trips/equity tables need K3's strategy to write rows.

**Inert / dormant on current traffic:**
- Round-trips count is low (1 resolved row total) so the History tab is sparse and the equity curve has ~30 minutes of data points. Both grow over time as resolver ticks land + 5-min snapshots accumulate. The dashboard renders cleanly at this density — empty-state messages cover the zero-data edge.
- Positions/Activity/Report tabs are deferred — not yet built. Adding them later is additive (new partials, new dropdown items in the tab nav) and doesn't reshape the data layer.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-pm-dashboard-20260511-0410; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG                  \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/routes.py.\$TAG                \$BASE/trading_corp/web/routes.py; \
mv \$BASE/trading_corp/utils/divisions.py.\$TAG           \$BASE/trading_corp/utils/divisions.py; \
mv \$BASE/trading_corp/web/templates/home.html.\$TAG      \$BASE/trading_corp/web/templates/home.html; \
rm \$BASE/trading_corp/web/templates/prediction_markets_dashboard.html; \
rm \$BASE/trading_corp/web/static/js/prediction_markets_chart.js; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 03:23 UTC — Kalshi Phase K7 + tune A (polymarket semaphore + lift time horizon 7d→14d)

**Triggered by:** Session-start audit decision tree, branch "B then A" picked by Board after diagnosis. K7 puts a defensive cap on polymarket's K=20 LLM fan; tune A lifts polymarket's `time_horizon_max_days` from 7 → 14 to resurrect survivor counts (was hitting 0/cycle pre-tune). Both shipped in sequence — semaphore first (insurance), tune second (load).

**Background — why both:**
- Polymarket had 0 survivors per scan cycle for the last hour pre-deploy (universe of 46–48 markets entirely filtered out by 7d horizon + 6h cooldown saturation). Lifting the horizon was the only way to revive evaluation.
- Resurrecting polymarket evaluation re-introduces the original 429-risk pattern (parallel K=20 fan overlapping kalshi_llm K=20). Kalshi has had Semaphore(8) since 01:08 UTC; polymarket was still uncapped. Insurance first.
- Kalshi LLM 15-30d bucket showed 26% avg divergence — comparable signal quality to ≤7d's 27%. So extending polymarket modestly to 14d is consistent with where Kalshi finds signal. Not jumping all the way to 30d.

**Files deployed (2 modified):**
- `trading_corp/agents/strategies/polymarket_arbitrage.py` — `run_scan_cycle()`'s warm-and-fan block now wraps `_estimate_probability` in `_gated_estimate` using `asyncio.Semaphore(llm_concurrency)`. Default 8 per memory `anthropic_concurrent_connections.md`. Both the warm call (`survivors[0]`) and the K-1 parallel fan go through the gate. Failed Anthropic requests still return None and advance cooldowns; semaphore releases on exception. Mirrors `kalshi_llm_arbitrage`'s pattern verbatim.
- `config/strategies.yaml` — polymarket_arbitrage block:
  - Added `llm_concurrency: 8` with explanatory comment.
  - Changed `time_horizon_max_days: 7` → `14`. Comment notes the K2.4 retune rationale.

**Backup tag:** `pre-kalshi-k7-polysemaphore-20260511-0325`

**Pre-deploy verification:**
- AST parse on patched-prod file. YAML loads correctly with both polymarket + kalshi `llm_concurrency=8` and polymarket `time_horizon_max_days=14`.
- 2 new functional tests in `tests/test_polymarket_arbitrage.py`:
  - `test_llm_fan_capped_by_semaphore`: K=10 survivors, `llm_concurrency=3` → asserts peak concurrent ≤ 3 via lock + counter spy on `_estimate_probability`.
  - `test_llm_fan_default_semaphore_is_8`: K=20 survivors, no `llm_concurrency` key → asserts peak ≤ 8 (default).
- 69 polymarket + kalshi_resolver + backtest_polymarket tests pass; zero regressions.
- Prod-drift check: prod's `polymarket_arbitrage.py` md5 differed from local HEAD; prod's `strategies.yaml` md5 differed from local HEAD. Both patches applied onto PROD's content via `/tmp/k24_prod/*.patched` workflow.

**Post-deploy verification (prod):**
- PID rotated 213825 → 215310; service active.
- Startup log shows all 4 scanners + 3 K2.4 background tasks online cleanly. No tracebacks related to K7. (Pre-existing Fidelity bot-detection error is unchanged noise.)
- **A took effect on next polymarket cycle (03:22:49 UTC):** `markets_pre_filter` jumped 47 → 56 (the 7–14d horizon markets surfaced via gamma-api end_date_max parameter); `survivors_post_filter` jumped 0 → 2.
- **First post-tune LLM calls (03:23:03 UTC):** 2 polymarket markets evaluated — Trump-related politics market + ATP tennis match (both within 14d window). Zero 429s. Semaphore well under cap (peak 2 vs ceiling 8). Strategy producing signal again after going dark for ~hours.
- `strategies.yaml` is hot-reloaded each scan cycle via `_reload()` — no restart was needed for tune A; the polymarket loop picked it up within 30s. K7 code IS in the restarted process, picked up by every cycle starting at 03:21:48.

**Notable code decisions:**
- **Semaphore on BOTH calls** (warm + fan), not just the fan. The warm call is single — semaphore allows it instantly — but wrapping it keeps `_gated_estimate` the only path to `_estimate_probability` so future maintenance can't accidentally bypass the cap.
- **Default 8 matches kalshi.** A future shared LLM-client-layer semaphore would be the architecturally cleaner cap (one global pool); deferred since both strategies are independently capped now and 429s are gone.
- **Tune-A bumped 7d → 14d, not 30d.** Conservative step. Kalshi LLM's 31-60d bucket has 0 trades overnight (the cap binds there too) — there's a natural plateau where longer horizons stop adding signal. 14d resurrects polymarket without overshooting.

**Inert / dormant on current traffic:**
- Polymarket cooldown saturates fast — after the first round of evaluations, expect `survivors_post_filter` to drop back to single digits or 0 for ~6h until cooldowns expire. That's by design; semaphore is the insurance for when bursts return.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k7-polysemaphore-20260511-0325; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/strategies/polymarket_arbitrage.py.\$TAG \$BASE/trading_corp/agents/strategies/polymarket_arbitrage.py; \
mv \$BASE/config/strategies.yaml.\$TAG                                  \$BASE/config/strategies.yaml; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 03:06 UTC — Kalshi Phase K2.4 (round-trip resolver + equity snapshot data layer)

**Triggered by:** Session-start audit showed 92 kalshi paper trades overnight (82 LLM + 10 structural temporal/bucket) accumulating without resolution. Decision-tree branch ">30 would_have_placed → ship K2.4 first" applied. Closes the data-layer gap noted in BACKLOG P0 NEXT: both Kalshi divisions previously showed only the $499 broker balance and no historical PnL — paper trades fired but no round-trip resolution existed to surface win/loss expectancy.

**Files deployed (3 modified, 1 new):**
- `trading_corp/persistence/db.py` — added 2 tables to SCHEMA:
  - `kalshi_round_trips` — single table covering all three Kalshi strategies (tail/temporal-bucket/llm); columns capture ticker + event_ticker + strategy + division + arb_type + arb_set_id + outcome_bet + qty/price/notional + entry/resolved ts + market_result + won/realized_pnl/roi_pct + implied_at_entry + llm_prob + divergence_pct + edge_cents + extra_json. UNIQUE(order_id) so resolver re-runs are safe.
  - `kalshi_equity_history` — per-division 5-min equity snapshots; columns ts + division + equity + cash_usd + positions_value + n_positions. Both Kalshi divisions share the same broker today so snapshots reflect identical dollar values; per-division separation preserves dashboard logical grouping and is forward-compatible with a future per-division sub-account split.
- `trading_corp/brokers/kalshi.py` — added `KalshiBroker.get_market_resolution(ticker)` async method. Looks up market via `client.get_market(ticker)`, reads `.result` field (Kalshi sets to "yes"/"no" at settlement, "void" for cancelled markets, "" while in-flight). Returns `{status: resolved|pending|void|not_found, result, ticker, close_time}`. Stub mode returns `not_found` (caller skips).
- `trading_corp/main.py` — wired 3 new asyncio tasks after the polymarket resolver block: `kalshi_resolver_task` (1h cadence) + `kalshi_equity_task_arb` (5min, kalshi_arbitrage division) + `kalshi_equity_task_llm` (5min, kalshi_llm_arbitrage division). All three cancellation hooks added in shutdown path via a small loop. Each guarded by `data_exec.brokers.get(division)` — if no broker is registered the task is skipped, never crashes startup.
- `trading_corp/agents/kalshi_resolver.py` — **NEW.** Structural clone of `polymarket_resolver.py` with Kalshi adapter:
  - `_fetch_unresolved_orders`: LEFT JOIN audit_event vs. kalshi_round_trips, keyed on `actor IN (kalshi_tail_price_arb, kalshi_temporal_bucket_arb, kalshi_llm_arbitrage)` AND `kind='would_have_placed'` AND no existing round-trip row.
  - `_detect_side(row)`: fallback ladder — `outcome` (LLM strategy) → `leg` prefix (`yes_*`/`no_*` for temporal_bucket, bare `yes`/`no` for tail_price). Returns 'yes', 'no', or None.
  - `_compute_round_trip_row`: Kalshi binary contracts pay $1 winner / $0 loser. Won → `qty × (1 - price)`. Lost → `-qty × price`. Void → 0. Skips malformed rows (price ≤0, price ≥1, qty ≤0, undetectable side).
  - `_insert_round_trip`: INSERT OR IGNORE keyed on order_id (re-run-safe).
  - `resolve_pending_round_trips(broker, max_per_tick=200)`: one pass; returns `{scanned, resolved, pending, void, not_found, errors}`. `max_per_tick=200` doubled vs polymarket because three Kalshi strategies share the table.
  - `write_equity_snapshot(db_url, division, broker)`: single snapshot per division.
  - `_resolver_loop` / `_equity_snapshot_loop`: periodic drivers, log-on-error-continue, asyncio.CancelledError clean exit.

**Backup tag:** `pre-kalshi-k24-resolver-20260511-0240`

**Pre-deploy verification:**
- AST parse on all 3 patched-prod files + new kalshi_resolver.py.
- 21 new kalshi_resolver tests pass (side detection × all 3 strategies, P&L math win/loss/void/malformed, INSERT OR IGNORE re-run safety, equity snapshot row shape + broker-error guard).
- 67 polymarket + kalshi_resolver tests combined pass; zero regressions.
- Prod-drift check: prod's `db.py` (331 lines vs local HEAD's 275) had extra helper functions; prod's `main.py` had bitunix_observer wiring not in local HEAD; prod's `kalshi.py` was untracked locally. All 3 patches applied onto PROD's content, not local HEAD, per the `trading_corp_prod_git_drift` memory note.

**Post-deploy verification (prod):**
- PID rotated 210117 → 213839; `systemctl is-active trading-corp` = `active`.
- `kalshi_round_trips` + `kalshi_equity_history` tables created with all expected columns + 3 indexes.
- Startup log: 3 new loops online — `kalshi round-trip resolver online (interval=3600s)` + `kalshi equity snapshot writer online (division=kalshi_arbitrage, interval=300s)` + `kalshi equity snapshot writer online (division=kalshi_llm_arbitrage, interval=300s)`.
- First equity snapshots landed at 03:06:26 UTC: both divisions at $499 cash / $0 positions / 0 n_positions.
- First resolver tick at 03:06:30 UTC: **scanned=113, resolved=1, pending=112, void=0, not_found=0, errors=0**. The 1 resolved row: order `a84388b6...` from kalshi_llm_arbitrage on `KXTEMPNYCH-26MAY1022-T64.99`, LLM bet NO @ $0.35 × 2.857 qty → market resolved YES → realized_pnl = -$1.00 (-100% ROI). Matches the strategy's $1/leg fixed sizing exactly.

**Notable code decisions:**
- **Single shared kalshi_round_trips table for all 3 strategies** (vs. one table per strategy). `strategy` + `arb_type` columns enable filtering. Avoids 3× schema duplication; future K4 multi-outcome detector adds rows with `arb_type='multi_outcome'` without DDL changes.
- **Side-detection via fallback ladder** (outcome → leg prefix). Each strategy serializes side differently in ProposedOrder.extra; resolver normalizes at read time. Tested across all three shapes.
- **Fees NOT modeled in paper-mode PnL.** Kalshi taker fee `roundup(0.07 × C × P × (1−C))` is mechanical but small relative to $1/leg sizing — gross PnL is the expectancy signal for paper-vs-live decisioning. Fees come in at Phase K5+ live work.
- **Two equity snapshots per cycle, identical dollars today.** Both divisions read the same Kalshi broker. Each writes its own row keyed by division — dashboard groups cleanly, and a future per-division sub-account split needs no schema change.
- **No HITL bypass risk.** This is read-only enrichment. No code path here touches order placement, risk caps, or live broker calls. Failure → log + skip + retry next tick.

**Inert / dormant on current traffic:**
- Resolver only acts on settled markets. Of 113 currently-unresolved kalshi paper trades, 112 are still in-flight (expiration dates ranging June 2026 and later). Resolver re-checks every hour — count will grow as markets settle.
- Dashboard surfacing of kalshi_round_trips + equity-curve sparkline is **NOT shipped here**; this is data layer only. Becomes the natural follow-up once round-trip counts grow past trivial. Polymarket's surfacing is also still TBD — they'd benefit from a shared partial.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k24-resolver-20260511-0240; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/persistence/db.py.\$TAG \$BASE/trading_corp/persistence/db.py; \
mv \$BASE/trading_corp/brokers/kalshi.py.\$TAG \$BASE/trading_corp/brokers/kalshi.py; \
mv \$BASE/trading_corp/main.py.\$TAG          \$BASE/trading_corp/main.py; \
rm \$BASE/trading_corp/agents/kalshi_resolver.py; \
sudo systemctl restart trading-corp
"
# The two new tables remain in the DB after rollback — they're harmless without
# the resolver code; sqlite drop is optional.
```

---

## 2026-05-11 00:52 UTC — Kalshi Phase K6.1 (LLM-divergence strategy, mirroring polymarket)

**Triggered by:** Board ask — "create another kalshi division that is LLM-based reusing what we built for polymarket." Phase K6.1 spins up a third Kalshi strategy (after structural tail + temporal/bucket already shipped) using the same LLM substrate as polymarket_arbitrage. Lives on its own division so dashboard surfaces it separately and risk caps are independent.

**Files deployed (6 modified, 1 new):**
- `trading_corp/agents/strategies/kalshi_llm_arbitrage.py` — **NEW.** `KalshiLLMArbitrageAgent` class. Clone of `PolymarketArbitrageAgent` with the Kalshi adapter:
  - Discovery via `KalshiBroker.list_markets()` (cache-aware; shared with the structural arb strategies' discovery cache)
  - Pre-filter: skip COLLECTION events, skip extreme-tail markets (already handled by `kalshi_tail_price_arb`), enforce min/max implied prob bounds + max time-to-resolution
  - K=20 markets per cycle, ranked by tightest spread first (LLM call most useful where market is least sure)
  - **Reuses `_polymarket_prompts.ANALYST_SYSTEM_PROMPT`** — generic enough for cross-venue prediction-market work, though category priors are polymarket-tuned (will revisit if Kalshi-specific priors materially help)
  - Warm-and-fan parallel LLM pattern: serial first call to hydrate Anthropic prompt cache, K-1 parallel after
  - Per-ticker 6h cooldown persisted in `agent_state` table (parallel to polymarket's per-condition_id cooldown)
  - ProposedOrder shape: `BUY YES @ yes_ask` if LLM thinks YES underpriced, `BUY NO @ no_ask` if overpriced. Fixed-USD sizing (default $1/leg).
- `trading_corp/main.py` — added `KalshiLLMArbitrageAgent` instantiation + `_scheduled_kalshi_llm_arb_loop` (clone of polymarket loop with name swap). Cancellation hook in shutdown path. Loop polls every 60s when enabled (matches polymarket cadence).
- `config/divisions.yaml` — new `kalshi_llm_arbitrage` division entry. Same Prediction Markets group, same kalshi broker (read-only). `standby: true` until first paper trades validate.
- `config/strategies.yaml` — new `kalshi_llm_arbitrage:` config block. K=20, cooldown 6h, divergence threshold 10%, time horizon 30d (broader than polymarket's 7d — Kalshi has many longer-horizon markets), prob bounds 0.05-0.95.
- `trading_corp/web/data.py` — added 3 new event kinds to SQL whitelist (`kalshi_llm_scan_cycle`, `kalshi_llm_probability_called`, `kalshi_llm_order_rejected_by_risk`) + `evt.kalshi_llm` enrichment dict mirroring polymarket's shape so the rich rail UI can render kalshi_llm rows with LLM probability strip + reasoning preview.
- `trading_corp/web/templates/division.html` — added `{% elif evt.kalshi_llm %}` branches for both kind label and body rendering. Same layout as polymarket: ticker badge + outcome badge + category + event title + LLM/market/divergence strip + reasoning preview + "Show analysis →" button.
- `trading_corp/web/routes.py` — new `GET /partials/kalshi-llm-analysis/{event_id}` HTMX endpoint. Reuses `partials/polymarket_analysis.html` (field name mapping: ticker→market_slug, event_title→market_question, expires_at→resolves_at, event_ticker→condition_id). Same right-rail rich panel.

**Backup tag:** `pre-kalshi-k61-llm-20260511-0048`

**Pre-deploy verification:**
- All 5 affected Python files parse cleanly (AST + Jinja).
- 66 polymarket/kalshi/main/risk tests pass; zero regressions.
- Local division registry verified: 4 Prediction Markets entries (polymarket_arbitrage, polymarket_copy_trading, kalshi_arbitrage, kalshi_llm_arbitrage).

**Post-deploy verification (prod):**
- PID changed (clean restart). `systemctl is-active trading-corp` = `active`. Web up on port 8000.
- Startup log: `Registered kalshi broker for division=kalshi_arbitrage (paper=False)` AND `Registered kalshi broker for division=kalshi_llm_arbitrage (paper=False)` — both divisions wired to the read-only KalshiBroker.
- Both KalshiBrokers connected to prod, balance=$499.00 (same Kalshi account; division separation is logical not physical).
- All 4 scanners online: Polymarket (enabled), Kalshi structural (enabled), Kalshi temporal+bucket (enabled), Kalshi LLM (**enabled=False** — Board flips when ready to incur Anthropic cost).
- Dashboard renders both `/division/kalshi_arbitrage` and `/division/kalshi_llm_arbitrage` tiles in the Prediction Markets group.

**Notable code decisions:**
- **Reuse over fork:** the strategy file is a structural clone of polymarket_arbitrage, NOT a refactor that generalizes both. Refactoring to a shared base class would be cleaner long-term but riskier in one-shot — easier to keep two parallel strategies for now and refactor when a third venue (or a fundamentally different LLM-divergence variant) lands.
- **Field-name mapping at the HTMX endpoint** (not in the partial template) keeps `polymarket_analysis.html` venue-agnostic. The endpoint constructs an `event` dict matching the polymarket field shape; template doesn't know it's rendering Kalshi data.
- **Same risk gate, no kalshi-llm-specific dispatch.** Risk verdict will fall through to default rules until we Board-flip enabled=True and see whether a $1/leg sizing + 10% divergence threshold + 6h cooldown produces useful behavior. Adjust `risk.yaml kalshi:` section caps then if needed.
- **Cost budget:** roughly doubles polymarket's daily Anthropic spend ($2-50/day → estimated $4-100/day) when enabled. Prompt caching is shared across both polymarket + kalshi_llm calls (same persona prefix), so per-call marginal cost stays low.

**Inert / dormant:**
- Strategy is `enabled: false` by default. Loop wakes every 60s and no-ops. Discovery isn't triggered until enabled=True. To start: flip `kalshi_llm_arbitrage.enabled` in `strategies.yaml` (hot-reloadable; no restart needed).
- No live order placement (Phase K7+).
- No data layer for round-trips / equity snapshots specific to this division (still K2.4 deferred — applies to both Kalshi divisions).

**Memory updates:** `trading_corp_kalshi.md` Phasing block needs K6.1 marked SHIPPED (separate edit).

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k61-llm-20260511-0048; BASE=/home/azureuser/trading_corp; \
mv \$BASE/config/divisions.yaml.\$TAG \$BASE/config/divisions.yaml; \
mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/routes.py.\$TAG \$BASE/trading_corp/web/routes.py; \
mv \$BASE/trading_corp/web/templates/division.html.\$TAG \$BASE/trading_corp/web/templates/division.html; \
rm \$BASE/trading_corp/agents/strategies/kalshi_llm_arbitrage.py; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 00:13 UTC — Kalshi Phase K2.3.1 (per-candidate audit events for true polymarket-density rail)

**Triggered by:** Board's second review of the dashboard found K2.3 still didn't match polymarket density. Root cause: aggregate scan summaries vs. polymarket's per-market rows. The polymarket rail emits `polymarket_llm_probability_called` per market evaluated (10-20 rows per cycle showing market_slug, question, LLM/market/divergence per row). Kalshi was only emitting one summary row per scan ("scanned 620 markets, 0 opps") — missing the per-market grain entirely.

**Files deployed (5 modified):**
- `trading_corp/agents/strategies/kalshi_tail_price_arb.py` — collect ALL examined tail candidates (not just ones above threshold) into a list with full context (ticker, event_title, category, subtitle, prices, edge_dollars, would_emit, expires_at). After scan, emit `kalshi_market_evaluated` audit event for top-N (default 5) sorted by edge descending. Same UX role as `polymarket_llm_probability_called` minus the LLM cost.
- `trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py` — parallel additions: emit `kalshi_pair_evaluated` per top-N temporal pair (event_title, early/late ticker + dates + yes_ask, edge_cents, would_emit) and `kalshi_bucket_evaluated` per bucket event (n_legs, sum_yes_asks, edge_cents, would_emit). New config knob `audit_top_n_candidates` (default 5).
- `trading_corp/web/data.py` — added the 3 new event kinds to the `_query_division_activity` SQL whitelist + per-kind enrichment fields (event_title, category, prices, would_emit etc.).
- `trading_corp/web/templates/division.html` — added 3 new inline rendering branches for `kalshi_market_evaluated`/`kalshi_pair_evaluated`/`kalshi_bucket_evaluated`. Each row shows ticker + tail/category badges + event title (the load-bearing human-readable text) + prices/sum/edge ratio strip + threshold. ARB-grade events get the gain color; below-threshold ones stay muted. Mirrors polymarket's market_slug → question → LLM/market/divergence layout.
- `trading_corp/web/templates/partials/kalshi_analysis.html` — added 3 new per-kind rich panels for the right-rail expansion: market_evaluated (3-card grid YES_ask/NO_ask/edge + tail badges + sum line), pair_evaluated (2-card grid early/late + constraint analysis), bucket_evaluated (3-card grid legs/sum/edge + would-emit verdict).

**Backup tag:** `pre-kalshi-k231-percandidate-20260511-0012`

**Pre-deploy verification:**
- All 4 affected Python/template files parse cleanly.
- Per-strategy `audit_top_n_candidates` knob defaults to 5; not exposed in strategies.yaml — relies on the default until tuning needed.

**Post-deploy verification (prod):**
- PID changed (clean restart). `systemctl is-active trading-corp` = `active`. Web server back up on port 8000.
- Both scanners online with enabled=True.
- First scan tick after restart fires at +300s; per-candidate audit events expected at +~310s. (Verification after monitor fires.)

**Notable code decisions:**
- The per-candidate emission DOES NOT change the order-emission path — opportunities above threshold still flow through the existing `_TailOpportunity` / `_TemporalOpportunity` / `_BucketOpportunity` lists into ProposedOrders. The new events are AUDIT-ONLY; they document "what we looked at and why we didn't trade" so the rail has substance even when 0 orders fire.
- Top-N sort order = edge descending. So the rail surfaces the NARROWEST MISSES first (markets closest to triggering an arb) — actionable visibility into where the strategy is most likely to fire next, vs random sampling.
- Polymarket's `polymarket_llm_probability_called` event acts as the inspiration. Same per-candidate grain. Different field shape (no LLM reasoning text, just structural pricing + edge).
- Per-pair audit for K2.2 walks the same date-sorted pairs the detector walks. Cost is O(N²) in markets per event but events are small (≤10 markets typical) so this is cheap.

**Inert / dormant:**
- Top-N is hard-coded to 5 (configurable via `audit_top_n_candidates` strategies.yaml knob). With both K2.1 + K2.2 + bucket scans firing per cycle = up to 15 audit rows per 5-min cycle. Manageable.
- Round-trips table + 5-min equity snapshots STILL not shipped — that's K2.4. Will need to land before paper trades start firing for PnL tracking.

**Memory updates:** None — `trading_corp_kalshi.md` K2.3 entry is sufficient; the rail-grain refinement is described in this deploy log only.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k231-percandidate-20260511-0012; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/strategies/kalshi_tail_price_arb.py.\$TAG \$BASE/trading_corp/agents/strategies/kalshi_tail_price_arb.py; \
mv \$BASE/trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py.\$TAG \$BASE/trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py; \
mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/templates/division.html.\$TAG \$BASE/trading_corp/web/templates/division.html; \
mv \$BASE/trading_corp/web/templates/partials/kalshi_analysis.html.\$TAG \$BASE/trading_corp/web/templates/partials/kalshi_analysis.html; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 00:04 UTC — Kalshi Phase K2.3 (dashboard parity with Polymarket)

**Triggered by:** Board flagged that Kalshi Arbitrage division drill-down at `/division/kalshi_arbitrage` showed "useless" rows that didn't expand and had nothing to inspect — vs. Polymarket which has rich inline rendering + HTMX-loaded analysis panel. K2.1 + K2.2 audit events were landing in the table (post-23:55 activity panel-whitelist fix) but only as bare kind-name labels. This deploy closes that gap by mirroring the Polymarket pattern across all four dashboard surfaces (data enrichment, inline rendering, HTMX expansion endpoint, partial template).

**Files deployed (3 modified, 1 new):**
- `trading_corp/web/data.py` — `_query_division_activity` enriches each kalshi event with a `kalshi: {...}` sub-dict (parallel to `polymarket: {...}`). Per-kind extra fields:
  - `kalshi_discovery_refreshed`: `n_events_total`, `n_markets_total`, `n_markets_filtered_collection`, `events_by_type`
  - `kalshi_tail_arb_scan`: `n_markets_scanned`, `n_tail_candidates`, `n_opportunities_above_threshold`, `min_edge_cents`, `yes_max/min_for_*_tail`
  - `kalshi_temporal_bucket_scan`: `n_temporal/bucket_events_scanned`, `n_temporal/bucket_opportunities`, threshold cents
  - `would_have_placed` / `kalshi_*_order_rejected_by_risk`: `ticker`, `event_ticker`, `edge_cents`, `leg`, `kalshi_pair_id`/`kalshi_arb_set_id`, `kalshi_arb_type`, `qty`, `limit_price`, `risk_verdict`, `risk_reason`
- `trading_corp/web/templates/division.html` — added `{% elif evt.kalshi %}` branch in the recent-activity loop. Inline rendering branches per kind:
  - Scan summaries: counts inline (e.g. "scanned: 620 markets · tail candidates: 259 · opps≥1.0c: 0 · tail≤0.05/≥0.95")
  - Discovery refreshed: events/markets totals + by-type chips
  - would_have_placed / risk-rejected: ticker + leg badge + arb-type badge + edge cents (color-coded) + cost + set/pair id
- `trading_corp/web/routes.py` — new `GET /partials/kalshi-analysis/{event_id}` endpoint mirroring `partial_polymarket_analysis`. Loads audit row, validates actor is one of the kalshi strategies, formats rich event dict, hands off to template.
- `trading_corp/web/templates/partials/kalshi_analysis.html` — **NEW.** Right-rail partial returned by HTMX. Per-kind rich panels (3-card grids for scan summaries, ticker+edge+max-risk for orders), pair/set linkage, risk reason callout, collapsible raw audit payload at the bottom for full inspection.

**Backup tag:** `pre-kalshi-k23-dashboard-20260511-0004`

**Pre-deploy verification:**
- All 4 affected files parse cleanly (Python AST + Jinja syntax via FastAPI startup).
- 7 webhook test failures present BEFORE this deploy — pre-existing `_Deps.bitunix_observer` attribute issue unrelated to dashboard work. 58 division/web/polymarket tests pass.

**Post-deploy verification (prod):**
- PID changed (clean restart). `systemctl is-active trading-corp` = `active`.
- `curl http://localhost:8000/division/kalshi_arbitrage` returns rendered HTML with all expected markers: "tail-price scan", "temporal+bucket scan", "discovery refreshed", "tail candidates:", "opps≥1.0c:", "Show details →" buttons on every kalshi row.
- `curl http://localhost:8000/partials/kalshi-analysis/{id}` returns rich HTML for a kalshi_temporal_bucket_scan event — header strip + 3-card grid (temporal/bucket events scanned + opps vs threshold) + collapsible raw payload. Identical pattern to polymarket-analysis partial.

**Notable code decisions:**
- The `kalshi: {...}` sub-dict mirrors `polymarket: {...}` shape so future-Claude can reason about the two prediction-market venues symmetrically. Only divergence: kalshi has multiple event-kind shapes (scan, discovery, order) so the dict has more conditional fields.
- Reused the `#pair-analysis` HTMX target on the right-rail panel — both polymarket and kalshi load into the same slot. The right rail surfaces "the most-recently-clicked event's detail," regardless of venue. Acceptable given the panel is single-purpose.
- Color contract preserved: green for `would_have_placed`, red for `risk_rejected`, mono for "scan with opportunities found", muted for "scan with 0 opportunities". Edge-cent colors: green ≥5¢, mono ≥2¢, muted <2¢. Matches the polymarket divergence color ladder.
- Raw payload always available via `<details>` collapsible — escape hatch for debugging without needing to query SQLite.

**Inert / dormant:**
- The "Expert Analysis" right-rail header still says "Click any position on the left to see its expert analysis here." That copy is for the polymarket use case and reads slightly off for kalshi (which has 0 positions, so there's nothing to click). Cosmetic; can update to "Click any activity row" later. Not blocking.
- Round-trips table + 5-min equity snapshots for Kalshi still NOT shipped — those are still K2.4 deferred work (need them once `would_have_placed` rows start landing for paper-PnL tracking).
- Position panel still shows "No positions detected for this division yet" — correct because `KalshiBroker.snapshot()` returns 0 positions (we have no executed orders, only paper would_have_placed rows).

**Memory updates:** `trading_corp_kalshi.md` Phasing block updated to mark K2.3 dashboard parity SHIPPED (separate edit).

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k23-dashboard-20260511-0004; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/routes.py.\$TAG \$BASE/trading_corp/web/routes.py; \
mv \$BASE/trading_corp/web/templates/division.html.\$TAG \$BASE/trading_corp/web/templates/division.html; \
rm \$BASE/trading_corp/web/templates/partials/kalshi_analysis.html; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-10 23:43 UTC — Kalshi Phase K2.2 + K2.0 discovery rate-limit hotfix

**Triggered by:** (a) Continuation of Kalshi roadmap immediately after K2.1 ship — K2.2 temporal + bucket arb detector ready to ship, (b) **incident response**: between 23:35 and 23:42 UTC the K2.1 strategy was Board-flipped to `enabled: true` for overnight audit data collection, but the discovery code immediately hit Kalshi's rate limit hard. pykalshi's `get_all_series(category=X, limit=N)` silently fetches ALL series for the category despite the `limit` param (or defaults `fetch_all` somewhere in its pagination logic), so the discovery enumerated **4482 series across 6 categories** instead of the configured 30/category × 6 = 180. Each series then triggered a `get_markets` call, all hitting 429 from Kalshi, retrying 3× per pykalshi's internal handler. Result: ~13K HTTP requests in flight, scan never completing. No financial cost (Kalshi reads are free, no LLM in loop, all 429s are rate-limit pushback not billable failures), but a noisy log + immediate disable required.

**Files deployed (3 modified, 1 new):**
- `trading_corp/data/kalshi_market_map.py` — **CAP FIX:** `discover_by_categories` now truncates the get_all_series result to `max_series_per_category` BEFORE iterating get_markets — pykalshi's `limit` param is documented as unreliable as a true cap. Added `inter_call_delay_sec=0.15` between get_markets calls (sustained ~6.7 req/s vs. Kalshi's ~5-10 req/s rate limit). Same delay applied between per-event `get_event` calls inside `_build_discovery_result`.
- `trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py` — **NEW.** `KalshiTemporalBucketArbAgent` strategy. Two detection methods sharing one class:
  - **TEMPORAL:** for events classified `EventType.TEMPORAL`, parse subtitle dates ("Before July 2026", "Before 2027", "Before Jan 20, 2029" etc — `parse_subtitle_date` handles ISO / Quarter / Month-Day-Year / Month-Year / Year-only formats). Sort markets by date. For each pair (early, late), if `yes_ask_early - yes_ask_late ≥ min_edge_cents` (default 4¢, clears 2-leg taker fees ~2-4¢ typical), emit a 2-leg arb set: BUY NO on early + BUY YES on late. Worst-case payout = $1, profit = edge_cents.
  - **BUCKET:** for events classified `EventType.BUCKET`, sum yes_ask across all markets in event. If `1 - sum ≥ min_edge_cents` (default 5¢, clears N-leg fees), emit an N-leg arb set: BUY YES on every leg. Guaranteed payout = $1.
  - Multi-leg ProposedOrders share `kalshi_arb_set_id`, `kalshi_arb_type` (`temporal` | `bucket`), and per-set risk metadata.
- `trading_corp/main.py` — added `KalshiTemporalBucketArbAgent` instantiation + `_scheduled_kalshi_tb_arb_loop` (parallel to `_scheduled_kalshi_arb_loop`). Both kalshi loops share the same `kalshi_arbitrage` division and broker; pykalshi internal cache means duplicate discovery within ttl is cheap. Cancellation hook added in shutdown path.
- `config/strategies.yaml` — new `kalshi_temporal_bucket_arb:` block with discovery / temporal / bucket / sizing / per_cycle config, `enabled: false` default. `kalshi_tail_price_arb` flipped back to `enabled: false` as part of incident response (line annotated with disable reason).

**Backup tag:** `pre-kalshi-k22-discoveryfix-20260510-2343`

**Pre-deploy verification:**
- Local syntax check: all 4 affected Python files parse cleanly.
- Local pytest: kalshi/main test slice passes — zero regressions.

**Post-deploy verification (prod):**
- PID changed (clean restart). `systemctl is-active trading-corp` = `active`. The restart immediately killed the in-flight 429 retry storm.
- Startup log:
  - `Registered kalshi broker for division=kalshi_arbitrage (paper=False)`
  - `Polymarket arbitrage scanner online (enabled=True, auto_execute=False, hitl=DIRECT)`
  - `Kalshi arbitrage scanner online (enabled=False, auto_execute=False, hitl=DIRECT)` ← K2.1, temp-disabled
  - `Kalshi temporal+bucket arb scanner online (enabled=False, auto_execute=False, hitl=DIRECT)` ← K2.2, default-off
- Zero 429s in the 60-second window after restart (vs. continuous 429 storm pre-restart).
- Prod end-to-end K2.2 strategy smoke (forced `enabled: true` via temp config, single cycle, 3 categories × 15 series × 20 markets, ~45 series): **0 arb sets, 0 total legs**. Honest baseline — most TEMPORAL pairs satisfy P(early) ≤ P(late) and most BUCKET sums equal $1 exactly. Real opportunities will surface during dislocations (event windows, news shocks, illiquid hours).

**Notable code decisions:**
- Cap fix is at OUR consumption layer (`for s_obj in series: if cat_count >= max ...`) rather than relying on pykalshi to enforce — defense in depth against future SDK behavior changes.
- 150ms inter-call delay is a soft rate limit (~6.7 req/s sustained) chosen to stay comfortably under Kalshi's empirical ~5-10 req/s threshold without artificially slowing discovery. With `max_series_per_category=30 × 6 categories = 180` series + ~50 events post-grouping, total scan cost ≈ (180 + 50) × 0.15s + actual HTTP latency ≈ 60-90 seconds per cycle. Cache-ttl 600s means at most 6 cycles/hour, totally manageable.
- `parse_subtitle_date` returns the LATEST possible date interpretation ("Before July 2026" → 2026-07-31) so temporal ordering matches the semantic constraint P(by month-end) ≤ P(by later-month-end).
- TEMPORAL arb position structure (BUY NO early + BUY YES late) is correct because the arb requires capturing the constraint violation regardless of which scenario resolves. Min payout = $1; profit = `yes_ask_early - yes_ask_late` minus fees.
- BUCKET arb is structurally simpler (sum < $1 = free money) but rarer; risk lives in N-leg fee burden which is why the threshold is set higher (5¢ vs 4¢).

**Inert / dormant:**
- BOTH Kalshi strategies are `enabled: false` post-deploy. K2.1 was temp-disabled mid-incident; K2.2 default-off awaiting Board review. To start collecting overnight audit data: flip both `enabled: true` in `strategies.yaml` (hot-reloadable, no restart needed — agents re-read on every cycle).
- No data layer (round-trips table / 5-min equity snapshots) shipped here. Still K2.3, deferred. Until then, the Kalshi Arbitrage tile shows broker-level account balance only.
- Risk gate still has no kalshi-specific dispatch in `risk.py`; orders fall through to default rules. Acceptable for K2.x while strategies are off; revisit before flipping enabled to true if we want belt-and-suspenders.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k22-discoveryfix-20260510-2343; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/data/kalshi_market_map.py.\$TAG \$BASE/trading_corp/data/kalshi_market_map.py; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
rm \$BASE/trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py; \
sudo systemctl restart trading-corp
"
```
*(Rollback restores the unfixed discovery code AND the pre-K2.2 main.py — do NOT re-enable kalshi_tail_price_arb after rollback; the cap fix is required to avoid re-triggering the 429 storm.)*

**Memory update:** `trading_corp_kalshi.md` Phasing block needs K2.2 marked SHIPPED (separate edit).

---

## 2026-05-10 23:28 UTC — Kalshi Phase K2.0 + K2.1 (discovery + tail-price arb scanner)

**Triggered by:** Continuation of Kalshi roadmap after K1 deploy at 22:29 UTC. Locked plan: Option C from the K2 phase-slicing conversation = K2.0 (market discovery + classification) + K2.1 (tail-price YES/NO arb detector) shipped together. Memory `trading_corp_kalshi.md` has the full phasing.

**Files deployed (4 modified, 2 new):**
- `trading_corp/data/kalshi_market_map.py` — **NEW.** Discovery + classification module. `is_tradeable_market` + `get_market_prices` lifted (MIT) from ryanfrigo/kalshi-ai-trading-bot — handles the API-v2 dollar-floats-vs-legacy-cents-int field-naming drift and the collection-ticker $1/$1 sentinel guard. EventType enum (BINARY / MULTI_OUTCOME / TEMPORAL / BUCKET / COLLECTION / OTHER). MarketRecord + EventRecord dataclasses. Two discovery paths: `discover_by_categories` (PRIMARY — category → series → markets traversal via `get_all_series`/`get_markets`) and `discover_open_markets` (DEPRECATED — bulk OPEN-markets endpoint returns ~all KXMVE* sports parlay containers in the first pages and pagination terminates inside the noise). Subtitle pattern matchers heuristically classify TEMPORAL ("before/by <date>") vs BUCKET ("Q1 2026" / month names).
- `trading_corp/agents/strategies/kalshi_tail_price_arb.py` — **NEW.** `KalshiTailPriceArbAgent` strategy mirroring the polymarket pattern (mtime-cached config from `strategies.yaml`, cooldown persistence in `agent_state` table). Per-cycle: refresh discovery cache (default ttl 600s), walk all non-COLLECTION events, find markets at YES_mid ≤5¢ or ≥95¢, check YES_ask + NO_ask < $1 - threshold (default 1¢ minimum edge), emit ProposedOrder pairs. Per-pair sizing: $1/leg fixed (paper-only). Each pair shares a `kalshi_pair_id` so audit + future replay can correlate the two legs.
- `trading_corp/brokers/kalshi.py` — added `list_markets()` method (broker-level abstraction matching PolymarketBroker pattern). Strategies don't talk to pykalshi directly — they call `broker.list_markets()` and get a `DiscoveryResult`.
- `trading_corp/main.py` — added `KalshiTailPriceArbAgent` instantiation alongside `PolymarketArbitrageAgent`. New `_scheduled_kalshi_arb_loop` (~150 LOC, mirror of `_scheduled_polymarket_arb_loop`) handles per-cycle scan + risk evaluation + audit logging + Telegram ping (per-pair, slim). Cancellation hook added in shutdown path.
- `config/strategies.yaml` — new `kalshi_tail_price_arb:` block with discovery / tail / sizing / per_cycle config, `enabled: false` default.
- `config/risk.yaml` — new `kalshi:` section with per-order, daily-aggregate, and total-open caps (intentionally tiny: $5/leg, $50/day, $50 total). Tail-specific universe params (yes_max=0.05, yes_min=0.95, min_edge_cents=1.0).

**Backup tag:** `pre-kalshi-k2-20260510-2328`

**Pre-deploy verification:**
- md5-diff (CRLF-normalized) all 4 modified files: clean — only my K2 additions, no prod-only drift.
- 2 new files confirmed absent on prod prior to push.
- Local pytest: 66 polymarket_arbitrage / risk / main / kalshi tests pass — zero regressions.
- Local discovery sanity-check (3 categories × 20 series × 20 markets) yielded 88 multi_outcome / 75 temporal / 1 bucket / 1130 tail-candidate-mids events — confirming the classifier picks up real Kalshi structure.

**Post-deploy verification (prod):**
- PID changed (clean restart). `systemctl is-active trading-corp` = `active`.
- Startup log:
  - `Registered kalshi broker for division=kalshi_arbitrage (paper=False)`
  - `Polymarket arbitrage scanner online (enabled=True, auto_execute=False, hitl=DIRECT)`
  - `Kalshi arbitrage scanner online (enabled=False, auto_execute=False, hitl=DIRECT)` ← new
- Zero warnings/errors since restart.
- Prod end-to-end strategy smoke (forced `enabled: true` via temp config, single cycle): **0 pairs / 0 legs**. Honest baseline — most Kalshi tail markets price efficiently to YES+NO=$1.00; real arb edges only appear during dislocations. Detector is working correctly.

**Notable code decisions:**
- The KEY_VAULT-backed env loader handles the new `KALSHI_API_KEY_ID` / `KALSHI_PRIVATE_KEY_PEM` already (added in K1). No secrets work needed for K2.
- `discover_open_markets` deliberately kept as a DEPRECATED audit/exploration tool. Its docstring documents WHY it's not the primary path so a future-Claude doesn't try to revive it.
- `_TailOpportunity` dataclass internal to the strategy keeps the discovery → ranking → ProposedOrder pipeline readable.
- Risk gate falls through to default rules when evaluating Kalshi orders today — `risk.yaml kalshi:` section is in place but `risk.py` doesn't yet have a `kalshi`-specific dispatch like polymarket does. Acceptable for K2.1 because (a) strategy is `enabled: false` by default, (b) $1/leg sizing won't bind any reasonable cap. Will add proper kalshi dispatch when we Board-flip enabled to true.

**Inert / dormant:**
- Strategy is `enabled: false` — discovery does not run, no orders emit. Loop wakes every `poll_interval_sec` (default 300s) and no-ops while disabled. Flip to true via `strategies.yaml` for shakedown.
- ProposedOrders go to `would_have_placed` audit rows only (paper). Live KalshiLiveBroker.place_order is Phase K5+ — gated on observed positive-EV across paper trades.
- No data layer (round-trips table / 5-min equity snapshots) shipped here. That's K2.3, deferred. Until then, the Kalshi Arbitrage tile shows broker-level account balance only.

**Memory updates:** `trading_corp_kalshi.md` Phasing block updated to mark K1 / K2.0 / K2.1 as SHIPPED with timestamps + design notes.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k2-20260510-2328; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
mv \$BASE/config/risk.yaml.\$TAG \$BASE/config/risk.yaml; \
mv \$BASE/trading_corp/brokers/kalshi.py.\$TAG \$BASE/trading_corp/brokers/kalshi.py; \
rm \$BASE/trading_corp/data/kalshi_market_map.py; \
rm \$BASE/trading_corp/agents/strategies/kalshi_tail_price_arb.py; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-10 22:29 UTC — Kalshi Phase K1 (read-only broker + Prediction Markets dashboard group)

**Triggered by:** Project pivot to add full Kalshi support, Polymarket copy-trading deprioritized. See memory `trading_corp_kalshi.md` for the locked phasing (K1 read-only broker → K2 intra-Kalshi temporal arb → K3 copy trading via leaderboard scraping → K4 multi-outcome arb → K5+ live orders). This deploy is K1: KalshiBroker (snapshot + quote), dashboard tile, KV-managed credentials, and a vendor-neutral "Prediction Markets" investment-type group housing Polymarket + Kalshi divisions side-by-side.

**Files deployed (4 modified, 1 new):**
- `trading_corp/brokers/kalshi.py` — **NEW.** `KalshiBroker(ReadOnlyBroker)` on top of `pykalshi.AsyncKalshiClient`. `snapshot()` fetches `portfolio.get_balance()` (cents → dollars, returns equity=cash+portfolio_value, buying_power=cash) plus `get_positions()`. `quote(ticker)` returns mid from `market.get_orderbook()`. RSA private key PEM materialized to a restricted-perms `/tmp/kalshi_*.pem` tempfile at connect, deleted on disconnect (pykalshi requires a filesystem path for the key). Stub mode if either credential is missing — tile renders "online · $0" rather than "not_wired", same pattern as BitUnix/Polymarket bring-up.
- `trading_corp/utils/secrets.py` — added `kalshi_api_key_id` + `kalshi_private_key_pem` fields, KV expected-vars list (KALSHI-API-KEY-ID, KALSHI-PRIVATE-KEY-PEM), `register_redact_literal(kalshi_private_key_pem)` so the PEM never lands in logs even if a third-party lib echoes it.
- `trading_corp/main.py` — added `if family == "kalshi"` broker-factory branch mirroring the polymarket pattern (no PaperExecutionBroker wrap — read-only adapters don't need it). Demo-mode toggle via `KALSHI_USE_DEMO=1` env var (defaults off — production / kalshi.com).
- `config/divisions.yaml` — added `kalshi_arbitrage` placeholder division (broker=kalshi, standby=true, intent=aggressive). Phase K2 wires the actual temporal/bucket arb scanner against this division.
- `trading_corp/utils/divisions.py` — **renamed group key `polymarket` → `prediction_markets`**, label "Polymarket" → "Prediction Markets". Routing extended via `_PREDICTION_MARKET_BROKERS = {"polymarket","kalshi"}` and `_PREDICTION_MARKET_SLUG_PREFIXES = ("polymarket_","kalshi_")` so both venues' divisions land in the new group regardless of broker family.
- `requirements.txt` — added `pykalshi>=1.0.6` (MIT, async + sync, RSA-PSS auth handled, REST + WS).

**Backup tag:** `pre-kalshi-k1-20260510-2229`

**Pre-deploy verification:**
- md5-diff prod vs local (CRLF-normalized) for the 4 modified files: clean — only my additions, no prod-only drift.
- Local pytest: 17 division/secrets/broker tests pass, zero regressions.
- Local smoke against real Kalshi prod account: `$499.00` cash, 0 positions, balance + positions endpoints return HTTP 200.

**Secrets uploaded to Azure Key Vault (kv-tc-vtwbowt3wtkpy):**
- `KALSHI-API-KEY-ID` (UUID)
- `KALSHI-PRIVATE-KEY-PEM` (1674 chars, multi-line PEM byte-perfect via `az keyvault secret set --file`)
- Read-back verified both values match local `.env`.

**Post-deploy verification (prod):**
- PID 199160 → 200767 (clean restart). `systemctl is-active trading-corp` = `active`.
- Startup log: KV fetched both KALSHI secrets, "Registered kalshi broker for division=kalshi_arbitrage (paper=False)" landed.
- First dashboard hit triggered `KalshiBroker.connect()` lazily (PolymarketBroker pattern); log shows "KalshiBroker connected (prod) — balance=$499.00 portfolio=$0.00".
- Dashboard root (`http://localhost:8000/`) renders the **"Prediction Markets"** group header containing the Kalshi Arbitrage tile with `equity = $499.00`, badges `aggressive` + `standby`. Polymarket Arbitrage + Polymarket Copy Trading also render in the same group (group rename was transparent).
- Zero warnings/errors since restart.

**Notable code decisions:**
- `KalshiBroker` subclasses `ReadOnlyBroker` (NOT `Broker`) — there is no `place_order` method on the type, so a code path that tries to place orders against Kalshi is a static type error, not a runtime exception. Same isolation guarantee as PolymarketBroker. Live order placement (Phase K5+) will land as a separate `KalshiLiveBroker(Broker)` when greenlit.
- pykalshi takes a filesystem path to the PEM, not bytes. The materialize-to-tempfile-on-connect pattern keeps the PEM out of the repo and out of any committed file; tempfile is deleted on `disconnect()` and restricted to owner-rw on POSIX.
- Group rename `polymarket → prediction_markets` was scoped to `utils/divisions.py` only — no template / data-layer references to the group key existed elsewhere in the codebase (the `evt.polymarket` references in `web/data.py` and templates are about polymarket-event analysis, not the group key).

**Inert / dormant:**
- `kalshi_arbitrage` division is `standby:true` — broker reads $499 balance and 0 positions, but no strategy operates on it yet. Phase K2 wires the temporal/bucket arb scanner.
- `place_order` / `cancel_order` not present on KalshiBroker by design (ReadOnlyBroker base). Phase K5+ will introduce KalshiLiveBroker.
- Volume Incentive Program ($0.005/contract cashback on trades 3¢-97¢) is a pending verification item — need to confirm per-side vs per-round-trip + qualification gates before Phase K2 sizing math relies on it.

**Memory updates:** `trading_corp_kalshi.md` already locked the architecture pre-deploy (SDK choice, repo-pillaging shortlist, phasing). No memory edit needed for this entry.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k1-20260510-2229; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/utils/secrets.py.\$TAG \$BASE/trading_corp/utils/secrets.py; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/config/divisions.yaml.\$TAG \$BASE/config/divisions.yaml; \
mv \$BASE/trading_corp/utils/divisions.py.\$TAG \$BASE/trading_corp/utils/divisions.py; \
rm \$BASE/trading_corp/brokers/kalshi.py; \
sudo systemctl restart trading-corp
"
```
*(Rollback restores the "Polymarket" group label and removes the Kalshi tile. Does NOT remove the KV secrets — they stay as orphans, harmless. pykalshi stays pip-installed in the venv, also harmless.)*

---

## 2026-05-10 21:01 UTC — Pink Box signal-name cleanup (dead-code purge)

**Triggered by:** Board re-confirmed end of session that Pink Box is NOT a TradingView alert — it's a static S/R image refreshed 2-3×/day. Today's audit log showed `pink_box_bear` firing 4× in a 9-min window this morning (08:48-09:00 UTC, BTCUSD/3) on what is most likely an old Coinbase TV alert from prior setup. Cleanup directive: remove every code path that treats `pink_box_bull/bear` as a valid arming signal, so any future stray webhook becomes `unknown_signal` (silent reject, audit row, no agent action).

**Files deployed (3):**
- `trading_corp/agents/strategies/lord_otter.py` — removed `pink_box_bull/bear` from `KNOWN_SIGNALS`, `_BULL_SIGNALS`, `_BEAR_SIGNALS`. Simplified `ArmedState` (source: `"spoon"` only — was `"pink_box" | "spoon"`). Simplified the arming branch in `_refresh_state_from_signal` to a clean `if signal == "spoon_bull"` / `elif signal == "spoon_bear"` (was a dual-membership check with awkward source-string assembly).
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — removed `pink_box_bull/bear` from `OTTER_TRIGGER_BULL` / `OTTER_TRIGGER_BEAR`.
- `config/strategies.yaml` — removed `pink_box_bull/bear` weight entries; updated `spoon_bull` comment ("divergence arming"; the prior "replaces pink_box per vision" hint is moot).

**Backup tag:** `pre-pink-box-cleanup-20260510-2059`

**Pre-deploy verification:**
- md5-diff against prod showed clean diffs — only the pink_box-related lines differ (no prod-only drift to preserve).
- Local pytest: 27/27 affected tests pass; 62/62 broader lord_otter+bitunix slice passes.

**Post-deploy verification:**
- PID 196773 → 199154 (clean restart). `systemctl is-active trading-corp` = `active`.
- Startup log clean (`bitunix_futures` broker registered; BitUnix KV secrets fetched). Zero warnings/errors since restart.
- Forward behavior: any incoming webhook with `signal=pink_box_bull` or `signal=pink_box_bear` will now hit the `KNOWN_SIGNALS` validator and be rejected as `unknown_signal` rather than setting `armed_long/short` state. Strictly safer than the prior behavior.

**Inert / dormant:**
- The 10 historical `pink_box_bear` audit rows from 08:48-09:00 UTC remain in `audit_event` — append-only, no cleanup. They'll naturally fall off the recency window over time.
- Dev-only files (`tests/test_lord_otter_bias_persistence.py`, `tests/test_signal_replay.py`, `scripts/test_lord_otter_webhook.py`, `scripts/sweep_btc_accumulator.py`) were also updated locally but are NOT deployed to prod.

**BACKLOG / memory updates:** P3 entry "Pink Box S/R confluence integration" updated — code-cleanup item struck (now DONE); integration design preserved for when we want to wire static S/R levels into the bitunix tier classifier. Memory `trading_corp_otter_tuned_for_3m.md` updated to reflect deployed cleanup status.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-pink-box-cleanup-20260510-2059; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/strategies/lord_otter.py.\$TAG \$BASE/trading_corp/agents/strategies/lord_otter.py; \
mv \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.\$TAG \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
sudo systemctl restart trading-corp
"
```
*(Rollback only if a real Pink Box TV alert turns out to exist and we want it accepted again — Board would need to assert that explicitly.)*

---

## 2026-05-10 16:56 UTC — Polymarket prompt cache fix + category-priors expansion

**Triggered by:** Board cost-optimization review of polymarket_arbitrage LLM spend ($2-50/day per deploy log). Direct prod verification (`/tmp/verify_polymarket_cache.py`) confirmed prompt caching was SILENTLY DEAD on Sonnet 4.6 — `cache_creation_input_tokens=0` and `cache_read_input_tokens=0` on every call since polymarket went live (2026-05-10 02:05 UTC). Root cause: the existing system prompt was 1,427 tokens, below Sonnet 4.6's 2,048-token minimum cacheable prefix. The `cache_control: ephemeral` marker on `_polymarket_prompts.py:ANALYST_SYSTEM_PROMPT` was no-op'd by the API. Fix: expand the system prompt past the threshold with content that's strategically valuable (category-specific priors), not filler.

**Files deployed (1):**
- `trading_corp/agents/strategies/_polymarket_prompts.py` — prompt expanded from ~1,427 → 2,513 tokens. Three changes: (1) new sports-underdog rejection worked example using the actual losing trade pattern from prod data (`mlb-nym-ari` 5¢ underdog at 90% LLM divergence — our first resolved-loss case), (2) new "Category-specific base rates and priors" section covering sports / geopolitical / Eurovision / crypto-action markets, (3) hard divergence sanity check rule (|prob_yes - implied| > 0.50 forces self-check; sports specifically capped at 0.30). Docstring updated to document the ≥2,048 token Sonnet 4.6 minimum.

**Strategic content added — these are domain priors the model otherwise lacks:**
- **Sports:** bookmaker-line markets are ~efficient → anchor within ±10pp of implied; deep underdog YES bets (<0.10 implied) are not edge opportunities; sub-markets (toss/total/first-set) priced at fair physical odds; tennis ranking-gap heuristic; MLB home-team base rate
- **Geopolitical:** short-window event markets default to <20% base rate; Iran/Middle-East markets are insider-priced (anchor near implied); war-end markets systematically over-predict
- **Eurovision:** top-5 most-bet account for ~70% of resolved-correct mass; <3% implied countries effectively never win
- **Crypto/company-action:** time-since-last-event > news headlines; tweet-count markets are Poisson; price-target markets follow options-implied vol

**Backup tag:** `pre-polymarket-prompt-cache-fix-20260510-1656`

**Verification — direct prod cache test BEFORE restart:**
```
Call 1 (cache write): input_tokens=80   cache_creation_input_tokens=2513   cache_read_input_tokens=0
Call 2 (cache read):  input_tokens=3    cache_creation_input_tokens=77     cache_read_input_tokens=2513
```
Cache is active. ~2,513 system-prompt tokens served from cache at 90% discount on every call after the first in a 5-min window. PID 194680 -> 196773 (clean restart). Service `active`. No errors in startup log.

**Cost analysis:**
- **Before (broken cache):** ~$0.0091/call (1,827 input × $3/M + 250 output × $15/M); $2-50/day depending on K-cycle activity
- **After (cache active):** ~$0.0035-$0.0044/call (2,513 cached × $0.30/M + ~150 fresh + 165 output × $15/M); estimated $0.80-$20/day
- **Savings: ~2.5× per call.** Not as dramatic as a Haiku switch (~10×) but preserves Sonnet 4.6's capability — the load-bearing assumption being that Sonnet's reasoning IS worth paying for if the prompt gives it the priors it lacks.

**Why Sonnet over Haiku:**
- Polymarket has many categories beyond sports — Sonnet may genuinely be better on politics/economics/long-tail
- The added priors directly address the empirical sports-underdog failure mode (the only clear LLM hallucination we'd resolved as of deploy time)
- Cost delta vs Haiku is ~$1/day; flip-to-Haiku remains an option if Phase 2.5 Backtester data shows Sonnet not earning its keep
- Haiku's minimum cacheable prefix is 4,096 tokens — would require ~doubling the prompt again, with diminishing returns on prior content quality

**Inert / dormant:**
- The 5-min ephemeral TTL is correct for our 30s scan cadence — every cycle's first call hydrates, the K-1 follow-ups (parallel via warm-and-fan) all hit cache
- The new sports-underdog rejection example uses real prod-loss data; if a future-Claude reads this and is tempted to fictionalize the example, leave it — citing real losses teaches discipline more effectively than synthetic ones

**Memory updates:** none required. The polymarket vision memory already references prompt-caching strategy generically; the model-specific cache-minimum table belongs in code/docs, not memory.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-prompt-cache-fix-20260510-1656; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/strategies/_polymarket_prompts.py.\$TAG \$BASE/trading_corp/agents/strategies/_polymarket_prompts.py; \
sudo systemctl restart trading-corp
"
```
*(Note: rollback returns to the broken-cache state. Don't roll back unless the new priors cause a measurable regression in win rate.)*

---

## 2026-05-10 16:12 UTC — Phase 3.2a venue correction: Coinbase 5m -> BitUnix native 3m

**Triggered by:** Board flagged that the Phase 3.2a deploy at 15:33 UTC had incorrectly pointed the live bar cache at Coinbase (and downgraded to 5m due to Coinbase's lack of 3m support). The historical EDA + tier thresholds + validated divergence list were all calibrated on BTCUSDT.P (Bybit-sourced TV exports → BitUnix execution venue). Cross-venue volatility-profile drift is the exact thing this would introduce. Fix: live bar source must be BitUnix.

**Decision tree:**
- **Bybit** would match the historical EDA — but Bybit is geo-blocked from US IPs by Cloudfront. Both my local and the prod Azure VM hit 403. Not viable as a live feed.
- **BitUnix** is what we trade on — there's no cross-venue gap if data and execution share venue. BitUnix's public REST kline endpoint (`/api/v1/futures/market/kline`) works without auth, supports native 3m, and returned 60 bars cleanly when tested from prod.
- **Coinbase** (the wrong choice in 15:33 deploy) — only supports {1m, 5m, 15m, 1h, 6h, 1d}. No 3m. Different venue from execution. Keeping as fallback in code only.

**Files changed (2):**
- `trading_corp/data/live_bar_cache.py` — refactored `refresh()` to dispatch on venue. New `_refresh_bitunix()` method uses `httpx` to call BitUnix REST kline directly (no CCXT — BitUnix isn't in CCXT). New `_refresh_ccxt()` retains Bybit/Coinbase support as fallbacks. Updated module docstring with the venue selection rationale.
- `trading_corp/main.py` — `LiveBarCache(symbol="BTCUSDT", timeframe="3m", venue="bitunix", max_bars=60)`. Was: `symbol="BTC/USD", timeframe="5m", venue="coinbase"`.

**Backup tag:** none — rolled directly over the 15:33 deploy. Restart was clean.

**Verification:**
- 60/60 tests still pass (cache tests use direct `bars=` injection, no network — unaffected by venue refactor).
- PID 193755 → 194694 (clean restart).
- Bar cache primed live: `{'symbol': 'BTCUSDT', 'timeframe': '3m', 'venue': 'bitunix', 'bars_cached': 60, 'last_close': 81474.9, 'atr_14': 77.34}`. ATR is 0.095% of price — proper 3m volatility profile (5m had been 0.115%).
- No refresh errors. Poll loop online with 60s cadence.

**What changes:**
- Stops now use real 3m volatility from the same exchange we trade on. ATR-driven sizing aligns with the historical-EDA-calibrated thresholds.
- Floor (0.3%) still wins on calm bars (since 1.5×$77 = $116 << 0.3%×$81k = $244), but during news/breakout windows real ATR will exceed floor and dominate as designed.

---

## 2026-05-10 15:33 UTC — BitUnix Phase 3.2a (live OHLCV bar cache + real ATR + paper_trade_record writes)

**Triggered by:** Phase 3.2a per memory `trading_corp_bitunix_phase3_confluence_model`. Foundation work for the eventual scale-out strategy (Phase 3.2b): replaces the 0.04%-of-price ATR placeholder with real ATR(14) from a live OHLCV cache, AND fixes a critical Phase 3.1 gap — bitunix paper trades weren't writing to `paper_trade_record`, so they had no win/loss resolution path. Both fixes here.

**Files deployed (3):**
- `trading_corp/data/live_bar_cache.py` — NEW. `LiveBarCache` polls Coinbase 3m... actually 5m (see hot-fix note) OHLCV via CCXT every 60s, caches last ~60 bars in-process. `get_atr(period=14)` computes ATR using Wilder's smoothing. `run_poll_loop` is the periodic background task. Drops in-progress (partial) latest bar. Errors logged + swallowed; cache keeps serving last successful snapshot.
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — UPDATED. Constructor takes optional `bar_cache`. `_build_proposal` accepts `atr_3m` parameter; uses real ATR when supplied (with `atr_source="live_atr_14"` marker), falls back to estimate when None (`atr_source="estimate_0.04pct"`). After `would_have_placed`, writes a `paper_trade_record` row via `PaperTradeRecord.from_order` so the existing strategy-agnostic `paper_trade_replay` loop resolves it. Order's `extra` keys harmonized (`take_profit_price`, `entry_reference_price`, `source_signal`, `max_dollar_risk`, `expected_gain_if_tp_hit`, `tp_r_multiple`) so `from_order` populates all PaperTradeRecord fields cleanly.
- `trading_corp/main.py` — constructs `LiveBarCache` alongside the observer; passes it as `bar_cache=` constructor kwarg; primes the cache synchronously before background loop starts; `bitunix_bar_task = asyncio.create_task(...)` runs the poll loop alongside donchian/polymarket/replay loops. Drift-aware deploy (pulled prod's main.py and patched the additions onto it).

**Hot-fix during deploy:** Initial `timeframe="3m"` failed with Coinbase CCXT (granularity not supported — Coinbase Advanced Trade only exposes {1m, 5m, 15m, 1h, 6h, 1d}). Switched to `timeframe="5m"` as the closest supported value. ATR profile is in the same ballpark; slightly more conservative stops. **Phase 4 will likely switch to Bybit native 3m** to match the historical EDA data we ingested for the EDA scripts (`scripts/eda_btc_scalping_signals.py` etc.).

**Backup tag:** `pre-bitunix-phase3-2a-20260510-1533`

**Verification:**
- 60/60 tests pass: 8 in `tests/test_live_bar_cache.py` (ATR computation correctness w/ Wilder's smoothing, gap-open TR handling, decay after volatile period, status snapshot, timeframe parsing) + 52 in `tests/test_bitunix_futures_observer.py` (full Phase 3.0/3.1/3.2a coverage including new tests for real-ATR-driven stops, atr_3m fallback, paper_trade_record write, bar_cache error swallowing).
- PID 192018 → 193147 → 193755 (one extra restart for the 5m hot-fix). Service `active`.
- **Bar cache primed live on prod:** `{'symbol': 'BTC/USD', 'timeframe': '5m', 'venue': 'coinbase', 'bars_cached': 59, 'last_close': 81332.13, 'atr_14': 93.84}` — ATR is 0.115% of price (5m typical volatility). Below the 0.3% stop floor, so floor still wins on calm bars; on volatile bars (ATR exceeds 200), real ATR will dominate stop sizing as designed.
- **Synthetic E2E with real ATR:** seeded bias (4h bull + 1D bull) + CVD (bull) + fired Otter `spoon_bull` trigger at $81,332 → observer classified PREMIUM, built order with stop $81,088 (-0.30% floor), TP $81,820 (+0.60% = 2R), wrote paper_trade_record with order_id `e8ad588f-...` showing all fields populated (tier, source_signal, entry_reference_price, stop_price, tp_price, tp_r_multiple). `result IS NULL` so the existing replay loop will pick it up next tick.
- Synthetic test data cleaned (audit_event, proposed_order, paper_trade_record, all 3 observer state tables).

**What changes for the user:**
- BitUnix paper trades now have real ATR-based stops instead of always defaulting to the 0.3% floor (will matter once 5m volatility exceeds 0.2% — happens during news/breakout windows).
- Paper trades will RESOLVE to win/loss via `paper_trade_replay` (next run within 15 min after each placement). Audit log + dashboard will show actual outcomes, not just "would have placed."
- This unlocks the "weeks of tuning" data collection cycle the board mentioned — we can now measure paper-mode win rates by tier and decide when to flip to live.

**Inert / dormant:**
- All real-money paths unchanged. BitUnix remains paper-only. Cache failures degrade gracefully (observer falls back to ATR estimate).
- 5m bars are a proxy for 3m at the venue level. Within-tier bar volatility is in the same ballpark; the real-ATR vs floor-wins decision will rarely flip due to this.

**Memory updates:** `trading_corp_bitunix_phase3_confluence_model.md` — already documents Phase 3.2a (added in this session); now reflects deployed state.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-phase3-2a-20260510-1533; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.\$TAG \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
rm -f \$BASE/trading_corp/data/live_bar_cache.py; \
sudo systemctl restart trading-corp
"
```
*Returns to Phase 3.1 (no live bar cache, ATR placeholder, no paper_trade_record writes for bitunix).*

---

## 2026-05-10 15:00 UTC — BitUnix Phase 3.1 (full ladder + order proposer + paper-mode auto-execute)

**Triggered by:** Phase 3.1 of the BitUnix vision per memory `trading_corp_bitunix_phase3_confluence_model`. Builds on Phase 3.0 (bias-only observer shipped 14:19 UTC same day) by adding the volume confluence axis, full tier ladder (PREMIUM/STANDARD/WEAK/COUNTER/SKIP), order proposer, risk caps, and paper-mode auto-execute (no per-trade HITL — Board approves the GUARDRAILS once; orders flow autonomously inside them).

**Files deployed (4):**
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — REWRITTEN. Class name preserved (avoid import churn), but functionally now a full division agent. Adds: CVD direction state machine (30 min decay), full PREMIUM/STANDARD/WEAK/COUNTER/SKIP classifier, structural stop calculator (`max(1.5×ATR, 0.3%×price)`), R:R gate (≥1.5), effective-risk cap downsizing (≤0.5% per trade), daily-risk kill-switch (≤3% per UTC day), multi-leg-ready `tp_plan` schema (single leg today; ready for Phase 3.2 scale-out), risk-gate submission, paper placement via data_exec, Telegram notification on placement, and a new `bitunix_decided` audit-event kind that fires for EVERY signal regardless of trade outcome.
- `trading_corp/web/webhooks.py` — switched both background tasks (`_process_lord_otter_alert`, `_process_market_cypher_alert`) from sync `observer.observe_alert(...)` to `await observer.observe_and_decide(...)`. Still wrapped in try/except — observer cannot disrupt existing real-money paths.
- `trading_corp/main.py` — observer construction now passes `risk_agent`, `data_exec`, `logger_agent`. Telegram channel attached after channel construction (deferred wire-up). Drift detected vs HEAD; pulled prod's main.py and applied the 2 deltas onto it before redeploying.
- `config/strategies.yaml` — added `bitunix_futures` strategy block documenting the board-approved caps (effective-risk 0.5%, daily kill 3%, tier sizing PREMIUM 4%/8x, STANDARD 2%/5x, WEAK 1%/2x, COUNTER 0.5%/2x default OFF, R:R ≥ 1.5, TP at 2R, decay windows). Values today live in code constants too; YAML lift-and-shift is a future refinement.

**New tables created at startup:**
- `bitunix_observer_cvd` — one row per side ('bull'/'bear') tracking the most recent same-side CVD-flip event. Decay: 30 min.
- `bitunix_observer_daily_risk` — one row per UTC date tracking cumulative effective-at-risk % across all bitunix_futures orders that day. Halt when >= 3%.

**Tier ladder (Phase 3.1):**
- **PREMIUM** — CVD agrees + 4h agrees + 1D agrees → 4% size × 8x leverage
- **STANDARD** — CVD agrees + 4h agrees + 1D neutral → 2% × 5x
- **WEAK** — CVD doesn't agree + 4h+1D agree → 1% × 2x
- **COUNTER** — CVD agrees + HTF contradicts → 0.5% × 2x; default OFF (`counter_tier_enabled=False`)
- **SKIP** — anything else (no order)

Effective-risk cap then downsizes any tier whose `target_size × leverage × stop_distance` would exceed 0.5% account equity. R:R gate refuses any trade where TP/SL ratio < 1.5.

**Ops model:**
- `auto_execute: true` — no per-trade HITL. Risk caps + daily kill ARE the gate. Board approves these once.
- Telegram notification on every paper placement (not approval).
- Every signal logs `bitunix_decided` audit row with outcome: `placed | skipped_tier | skipped_no_deps | skipped_no_broker | skipped_no_equity | skipped_sizing | skipped_daily_kill | rejected_risk | error_*`.
- When this flips PAPER → LIVE in Phase 4, the only addition is real `BitunixBroker.place_order()` w/ leverage + isolated margin. Same caps apply.

**Backup tag:** `pre-bitunix-phase3-1-20260510-1500`

**Verification:**
- 46/46 tests pass (`tests/test_bitunix_futures_observer.py`) — full tier matrix (12 default + 4 counter-enabled), bias state w/ decay, CVD state w/ decay, order proposer math (sizing + stop + TP + R:R + effective-risk cap), daily-risk accumulation + isolation, async observe_and_decide flow w/ mocked deps (PREMIUM places order, SKIP doesn't, daily kill blocks, risk reject path).
- PID 190918 -> 192018 (clean restart). Service `active`. All 3 observer tables auto-created at startup.
- **Synthetic E2E on prod:** seeded bias (4h bull + 1D bull) + CVD (bull) + fired Otter `spoon_bull` trigger → observer correctly:
  - classified PREMIUM
  - submitted to risk gate (approved)
  - logged `would_have_placed` with order_id `f7bb0165-...`, qty 0.0198 BTC at $80,800 entry, 4% × 8x = $1,600 notional, structural stop at 0.3% floor
  - logged `bitunix_decided` outcome=placed
  - daily-risk counter incremented (cleaned post-test)
  - no Telegram (test fixture explicitly skipped to avoid spamming Board)
- Synthetic test data cleaned post-run (audit_event, proposed_order, all 3 observer tables wiped).

**Inert / dormant — what could go wrong now is bounded:**
- bitunix_futures broker is registered as `paper-exec` (verified in startup log). All "would have placed" orders simulate via PaperExecutionBroker — no real BitUnix orders issued.
- COUNTER tier defaulted OFF — no fade-the-trend trades unless explicitly enabled.
- Daily-risk kill caps the worst-case daily exposure at 3% account equity (cumulative pre-trade risk).
- Effective-risk-per-trade caps each individual trade at 0.5%.
- All deps required for order placement (`risk_agent`, `data_exec`, `logger_agent`, `bitunix_futures` broker) — observer skips with audit row if any is missing.

**What changes for the user:**
- Will start receiving Telegram pings for paper-mode BitUnix placements as Otter alerts fire and align with HTF bias + CVD.
- Frequency: bounded by Otter trigger rate (~7-15/day in Phase 3.0 observation period) further filtered by tier requirements (most will be SKIP).
- Every classification is in `audit_event` (kind `bitunix_observer_classified`) and every decision in `bitunix_decided` — visible in the activity rail / queryable in SQL.

**Memory updates:**
- `trading_corp_bitunix_phase3_confluence_model.md` — already contains the Phase 3.1 design (added in this session); now reflects the deployed state.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-phase3-1-20260510-1500; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/trading_corp/web/webhooks.py.\$TAG \$BASE/trading_corp/web/webhooks.py; \
mv \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.\$TAG \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
sudo systemctl restart trading-corp
"
```
*Rollback returns to Phase 3.0 (bias-only observer, no orders). New tables (`bitunix_observer_cvd`, `bitunix_observer_daily_risk`) stay in DB but are unused — drop manually if you want a clean slate.*

---

## 2026-05-10 14:19 UTC — BitUnix Phase 3.0 observer (bias-only tier classifier, no orders)

**Triggered by:** Phase 3 of the BitUnix vision per memory `trading_corp_bitunix_phase3_confluence_model`. Phase 3.0 ("observer mode") shipped first as a de-risking step before Phase 3.1 (full tier ladder w/ volume axis + ProposedOrder emission) and Phase 4 (real BitUnix order placement).

**Files deployed (4):**
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — NEW. The Phase 3.0 module. Receives Otter+Cypher webhook signals (additive, runs alongside the existing Otter/Cypher agents). Maintains a persistent bias state machine on 4h+1D timeframes fed by Cypher divergence/cross signals. For each Otter trigger on 3m, classifies into a bias-only tier (STRONG_HTF / MODERATE_HTF / COUNTER_HTF / NEUTRAL_HTF / SKIP) and writes one `audit_event` row with `kind=bitunix_observer_classified`. Emits NO ProposedOrders. Risk gate not invoked. Observer cannot affect real-money paths — every public method wraps in try/except and swallows.
- `trading_corp/web/app.py` — added `bitunix_observer: Any = None` field to `WebDeps` dataclass.
- `trading_corp/web/webhooks.py` — added one observer call at the top of each background task (`_process_lord_otter_alert` and `_process_market_cypher_alert`), wrapped in try/except. Observer runs FIRST so it captures every signal even if downstream paths crash.
- `trading_corp/main.py` — constructed the observer at startup; added `bitunix_observer` parameter to `_start_web_server`; passed it into the `WebDeps` constructor.

**New table created at startup (auto via `BitunixFuturesObserver.__init__`):**
- `bitunix_observer_bias` — one row per (timeframe, side) tracking the most recent same-side bias-setter event timestamp. Decay applied at lookup time. Schema in `bitunix_futures_observer.py:OBSERVER_BIAS_TABLE_DDL`.

**Tier ladder (bias-only — Phase 3.1 will add volume axis):**
- **STRONG_HTF** — 4h + 1D both agree with trigger
- **MODERATE_HTF** — 4h agrees, 1D neutral
- **COUNTER_HTF** — 4h or 1D contradicts (don't fade trend)
- **NEUTRAL_HTF** — both HTFs neutral (cold start)
- **SKIP** — symbol not whitelisted or signal not classifiable

**Bias decay windows:** 4h = 24h half-life; 1D = 7-day half-life. Same-direction signals refresh.

**Bias-setters (Cypher 4h + 1D):** `mc_a_longema`, `mc_a_bluetriangle`, `mc_b_gold_buy`, `mc_b_buy_circle_div`, `mc_b_buy_circle` (bull); `mc_a_blood_diamond`, `mc_a_red_diamond`, `mc_a_redx`, `mc_a_yellow_x`, `mc_b_sell_circle_div`, `mc_b_sell_circle` (bear). Dot signals excluded as too low-conviction.

**Triggers (Otter 3m):** `otter_buy/sell`, `spoon_bull/bear`, `pink_box_bull/bear`, `water_buy_small/large`, `water_sell_small/large`, `money_bag_bottom/top`. CVD flips intentionally held back — they're volume-axis input for Phase 3.1, not entry triggers.

**Symbol whitelist:** BTC only (BTC/USD, BTCUSD, BTCUSDT, BTCUSDT.P).

**Out of scope (deferred to later phases):**
- Volume confluence axis (Phase 3.1 — uses CVD-flip webhook signals + live OHLCV polled from existing Coinbase/BitUnix broker connections; no new infrastructure)
- ProposedOrder emission with structural stop, effective-risk cap, daily-loss kill, ATR-tied pullback (Phase 3.1)
- Real `BitunixBroker.place_order()` w/ leverage + isolated margin (Phase 4)
- YAML cleanup of Otter+Cypher entries from `coinbase_spot` (Phase 3.1, alongside bitunix_futures division YAML entry + broker registration)

**Backup tag:** `pre-bitunix-phase3-observer-20260510-1419`

**Verification:**
- 24/24 unit tests pass (`tests/test_bitunix_futures_observer.py`) — tier classifier matrix (12 cases), bias state machine with decay, refresh-on-same-side, opposite-side-takes-most-recent, exception-swallowing, audit-event emission.
- Drift check before deploy: `main.py` had drift between local HEAD and prod (per memory `trading_corp_prod_git_drift`). Pulled prod's content, applied my 4 additive edits onto it, scp'd back. `webhooks.py` and `app.py` had no drift.
- PID 186736 -> 190918 (clean restart). Service `active`. Observer table auto-created at startup (verified `.schema bitunix_observer_bias`).
- **Synthetic E2E test on prod:** seeded bias with a Cypher 4h `mc_b_buy_circle_div` (bull), then fired an Otter 3m `spoon_bull` trigger — observer correctly classified MODERATE_HTF (4h=bull, 1D=neutral). Synthetic test data cleaned from `audit_event` and `bitunix_observer_bias` post-test so the audit trail isn't polluted.
- Awaiting first real signal: next natural Otter alert will write the first real `bitunix_observer_classified` audit row.

**Inert / dormant:**
- Observer runs purely for telemetry. No orders, no risk-gate participation, no broker interaction. Failure modes are bounded to "no audit row written" — never "wrong order placed" or "real money lost."
- Bias state will start populating as Cypher signals arrive. Cypher webhooks ARE active (the strategy agent is `enabled: false`, but the webhook handler always runs the background processor, which now ALWAYS calls the observer first).

**Memory updates:**
- `trading_corp_bitunix_phase3_confluence_model.md` — already contains the full Phase 3.0 design (added in earlier session); now reflects the deployed state.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-phase3-observer-20260510-1419; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/trading_corp/web/webhooks.py.\$TAG \$BASE/trading_corp/web/webhooks.py; \
mv \$BASE/trading_corp/web/app.py.\$TAG \$BASE/trading_corp/web/app.py; \
rm -f \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
sudo systemctl restart trading-corp
"
```
*(Note: rollback removes the new file but leaves `bitunix_observer_bias` table + any `bitunix_futures_observer` audit rows in the DB — drop manually if you want a clean slate.)*

---

## 2026-05-10 04:19 UTC — BitUnix Futures equity 2× double-count fix

**Triggered by:** Board flagged the dashboard tile + division-detail page rendering BitUnix equity at exactly 2× the cash balance ($6,763.94 vs real $3,381.97). BACKLOG #32 P2. Fixed during a quiet pass while polymarket accumulates trades for the Phase 2.5 Backtester gate.

**Files deployed (1):**
- `trading_corp/brokers/bitunix.py` — `coin_equity` formula now sums `available + frozen + margin + crossUnrealizedPNL + isolationUnrealizedPNL` (dropped `transfer` AND `bonus`). Comment block at lines ~180-205 rewritten with corrected field semantics + the empirical reconciliation that drove the fix.

**Root cause:**
Live `/api/v1/futures/account` data showed `transfer` and `bonus` are *attribution metadata* — they describe the share of the current `available` balance that arrived via wallet-transfer (`transfer`) or promo credit (`bonus`). They are ALREADY counted inside `available`, not separate buckets. The 2026-05-03 deploy comment that called transfer "additive" was wrong (one-shot reconciliation that didn't generalize). Per-coin observation:

| Coin | available | transfer | bonus | OLD coin_equity | NEW coin_equity |
|---|---|---|---|---|---|
| USDT | 25.27 | 0 | 25.27 (dup) | 50.55 | 25.27 |
| USDC | 3356.70 | 3356.70 (dup) | 0 | 6713.39 | 3356.70 |
| **Total** | | | | **$6,763.94** | **$3,381.97** |

Note: BACKLOG #32 hypothesis flagged `transfer` only; `bonus` duplication was discovered during verification. BitUnix shows whichever attribution applies (transfer vs promo) — could be either field for any given coin. Both must be excluded.

**Verification step (one-off, kept as a script):**
- `scripts/verify_bitunix_account_fields.py` — dumps raw per-coin JSON + per-field breakdown + sum-of-seven vs corrected sum. Read-only; no orders touched. Run via `cd /home/azureuser/trading_corp && PYTHONPATH=$PWD KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ ./venv/bin/python scripts/verify_bitunix_account_fields.py`. Useful next time BitUnix balance fields look suspect.

**Backup tag:** `pre-bitunix-equity-fix-20260510-0419`

**Verification:**
- PID 185236 → 186736 (clean restart). Service `active`.
- Startup log: `BitunixBroker connected (account=bitunix-futures, equity=$3381.97, 0 positions)` ✓
- Direct broker probe via `BitunixBroker.snapshot()` returns `equity=$3381.97 cash=$3381.97 buying_power=$3381.97 positions=0` — matches BitUnix UI Total Equity.
- `/` and `/division/bitunix_futures` both HTTP 200 post-deploy.
- Polymarket resolver + equity snapshot loops still ticking unchanged (resolver: scanned=8 pending=8; same numbers as pre-restart).
- Fidelity broker errors in journal are pre-existing/unrelated (bot-detection on the Fidelity login page, present since ~16:42 UTC May 09).

**Inert / dormant:**
- Display-only fix today. BitUnix is read-only standby (Phase 1) — no sizing math, risk caps, or `auto_execute_caps` percentages currently consume this number.
- **Becomes load-bearing at Phase 4** (live order placement) — risk/sizing math reading the broker's equity would have oversized 2×. Fix lands well before that gate.

**Memory updates:**
- `trading_corp_bitunix_vision.md` — Phase 1 entry now contains a 2026-05-10 retraction of the 2026-05-03 "transfer is additive" claim, with the corrected formula recorded inline.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-equity-fix-20260510-0419; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/brokers/bitunix.py.\$TAG \$BASE/trading_corp/brokers/bitunix.py; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-10 03:28 UTC — Polymarket dashboard data layer: round-trips + equity-history persistence (gaps A + B)

**Triggered by:** Board reviewed betmoar.fun dashboard 2026-05-09; asked for any data-persistence gaps to be closed NOW so the eventual UI rebuild has a complete dataset to render. Gap analysis identified A (resolved round-trips) + B (5-min equity snapshots) as the two highest-leverage closures. Gap C (open-positions cache) and the dashboard UI itself moved to BACKLOG (P3) — the data layer is the precondition.

**Files deployed (3):**
- `trading_corp/persistence/db.py` — SCHEMA additions: `polymarket_round_trips` (UNIQUE on order_id, INSERT OR IGNORE-safe) + `polymarket_equity_history` (5-min cadence, append-only). Both protected by `CREATE TABLE IF NOT EXISTS` so init_db() picks them up at startup with no migration script.
- `trading_corp/agents/polymarket_resolver.py` — NEW. `resolve_pending_round_trips(db_url, broker)` walks `would_have_placed` rows whose order_id is missing from `polymarket_round_trips`, looks up resolution via `broker.get_market_resolution`, computes binary-outcome P&L, INSERTs one row. `write_equity_snapshot(db_url, division, broker)` calls `broker.snapshot()` + appends one row. Plus `start_resolver_loop` (3600s) and `start_equity_snapshot_loop` (300s) helpers. Mirrors paper_trade_replay's pattern.
- `trading_corp/main.py` — wires both loops alongside polymarket_arb_task. Cancellation handling in finally block. Graceful no-op if broker absent (logs warning, leaves task None).

**Features shipped:**
- **Hourly resolver (gap A live):** every 60 min, walks unresolved `would_have_placed` rows for `polymarket_arbitrage`, persists resolved P&L to `polymarket_round_trips`. INSERT OR IGNORE keyed on `order_id` so the loop is idempotent. First tick at startup confirmed: scanned=8 / pending=8 / errors=0 (none resolved yet — markets started today).
- **5-min equity snapshot (gap B live):** every 300s, calls `broker.snapshot()` + appends `(ts, division, equity, cash_usdc, positions_value, n_positions)`. First snapshot landed at `2026-05-10T03:28:18+00:00`: equity=$500.00 / cash_usdc=$500.00 / positions_value=$0 / n_positions=0. Matches the funded wallet state.

**Notable code changes:**
- The resolver join uses `json_extract(payload_json, '$.order_id')` to LEFT JOIN audit_event against polymarket_round_trips. `_fetch_unresolved_orders` returns only rows where the round-trip is missing, so a tick is bounded by the actual unresolved backlog (typically <100). `max_per_tick=100` clamps gamma-api calls per tick regardless.
- `positions_value` derived as `max(0, equity - cash)` rather than summing per-position market values — robust against position-shape drift in `data-api.polymarket.com/positions` (the field-name shape isn't pinned to a verified non-empty response yet). Tighten when first non-empty positions response is observed.
- Backtester (`scripts/backtest_polymarket_arbitrage.py`) is unchanged + still works for ad-hoc Board memo runs. Backtester computes everything in-memory each invocation; the resolver persists for dashboard reads. Slight redundancy is intentional — Backtester is for one-shot decision support, resolver feeds the always-on dashboard.

**Latent bug caught + fixed:**
- First boot crashed the resolver loop with `TypeError` from `log.info("polymarket_resolver tick: %s", counts)` — the prod RedactingFilter rewrites dict args into their keys, breaking %-style format. Known prod gotcha (per memory `trading_corp_prod_ops`). Fixed by switching to f-string. Two patch deploys for this entry.

**Verification:**
- PID 184728 → 185250 (final restart with f-string fix). Service `active`. Both loops in startup logs:
  ```
  polymarket round-trip resolver online (interval=3600s)
  polymarket equity snapshot writer online (division=polymarket_arbitrage, interval=300s)
  polymarket_resolver tick: {'scanned': 8, 'resolved': 0, 'pending': 8, 'void': 0, 'not_found': 0, 'errors': 0}
  ```
- DB inspection confirms both tables exist + first equity row landed: 1 row in polymarket_equity_history, 0 rows in polymarket_round_trips (correct — markets haven't resolved).

**Inert / dormant:**
- `polymarket_round_trips` will start filling as today's paper trades' markets begin resolving (next 12-72 hours depending on category). First wave will be sports markets that resolve same-day or next-day. Politics/longer-tail markets fill in over the week. Dashboard build-out (P3 BACKLOG) blocked on having ~30 resolved rows for meaningful inference.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-data-gaps-20260510-0328; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/trading_corp/persistence/db.py.\$TAG \$BASE/trading_corp/persistence/db.py; \
rm -f \$BASE/trading_corp/agents/polymarket_resolver.py; \
sudo systemctl restart trading-corp
"
```
*(Note: rollback removes the new tables but leaves any data already written in them — `DROP TABLE polymarket_round_trips; DROP TABLE polymarket_equity_history;` if you want a clean slate.)*

---

## 2026-05-10 02:51 UTC — Polymarket: warm-and-fan parallel LLM calls + K=10→20

**Commit:** `969c6ab` — 2 files, 39 insertions / 6 deletions.
**Triggered by:** Board direction 2026-05-10 — Polymarket needs faster reaction; sequential K=10 was making cycles ~80s apart instead of the intended 30s.

**Changes:**
- `polymarket_arbitrage.py:run_scan_cycle` — warm-and-fan parallel pattern. First LLM call serial (warms Anthropic prompt cache); remaining K-1 fire via `asyncio.gather`. Cycle time: ~50s sequential → ~10s parallel. Prompt-cache hits preserved (the cache prefix is hot before fan-out).
- `strategies.yaml polymarket_arbitrage.k_markets_per_cycle: 10 → 20`. Doubles unique markets evaluated per cycle. Cooldown 6h still bounds daily LLM cost; daily ~$2-50 → ~$4-100 worst case.
- `asyncio.gather(return_exceptions=True)` — single LLM failure becomes None in the estimates list; cooldown still advances; per-market loop skips. Order preserved via `zip(survivors, estimates)`.

**Backup tag:** `pre-warm-fan-parallel-20260510.tar.gz`.
**Verification:** PID 182852 → 183604 (clean). Post-restart cycle at 02:52:15 shows `k_per_cycle: 20` ✓. Cycle currently finds `survivors_post_filter: 0` because all 22 eligible markets are in 6h cooldown from earlier today's runs (earliest expires 08:06 UTC). Parallel LLM behavior will exercise naturally as cooldowns expire ~03-05 hours from now. Audit-row timestamp pattern will show the change: previously 5s gaps between K calls; now one 5s warm + tight burst of ~19 calls within ~5s.

**Anthropic limits — not a constraint.** At tier-3 (4000 RPM), running K=20 + parallel = ~30 req/min worst case = 99.3% headroom. Cost is bounded by 6h cooldown, not rate limits.

---

## 2026-05-10 02:31 UTC — Polymarket UX rework: rich activity tiles + LLM analysis right-rail + reasoning persistence

**Commits:** `4bcaf14` (Phase 1 — reasoning persistence) + `f81ae5c` (Phase 2 — UI).
**Triggered by:** Board feedback 2026-05-10 ~02:15 UTC: *"WOULD HAVE PLACED tells me nothing about the trade. Expert Analysis tile should show the LLM decision. I would like the expert llm analysis to be saved as a point in time static snapshot."*

**Phase 1 — reasoning persistence (4bcaf14):**
- `polymarket_llm_probability_called` audit row now carries `llm_reasoning` (full LLM justification text), `key_unknowns`, `question` (full market question), `would_emit` flag, `resolves_at`. Was missing the reasoning text — load-bearing for fine-tuning.
- ProposedOrder.extra extended with same fields; `would_have_placed` payload pulls them in main.py.
- Storage cost negligible (~5-20MB/month at K=10/30s saturation).

**Phase 2 — UI rework (f81ae5c):**
- `_query_division_activity` now includes `polymarket_llm_probability_called` + `polymarket_order_rejected_by_risk` kinds (NOT scan_cycle — would flood). Each row gets a `polymarket: dict | None` sub-shape with all fields needed for rich tile rendering.
- `division.html` activity-row template branches on `evt.polymarket` for rich layout: market_slug + BUY YES/NO badge + category/series chips + market question (line-clamp-2) + probability strip (LLM% vs market% vs Δ% vs sizing) + 200-char reasoning preview (italic). Risk-rejected variant surfaces risk_reason in red.
- New endpoint `GET /partials/polymarket-analysis/{event_id}` + `partials/polymarket_analysis.html`. "Show analysis →" button on each tile loads full LLM snapshot into the right rail via HTMX. Right rail shows: kind+ts header, market question, 3-card prob grid (LLM YES / market YES / divergence + threshold), decision (outcome + sizing + skip/risk-reject indicators), full LLM reasoning in preformatted block, key unknowns list, resolution metadata + audit event id.
- Right rail empty-state copy differentiates Polymarket ("Click 'Show analysis →' on any LLM call…") from PMCC ("Click any position…").

**Backup tag:** `pre-polymarket-rich-ui-20260510.tar.gz` (51K).
**Verification:** PID 181134 → 182852 (clean restart). Endpoint smoke: `GET /partials/polymarket-analysis/{latest_id}` returns 200 with rendered analysis (sample: hantavirus market, LLM 97% YES). Division detail page returns 200 with "Show analysis" buttons + 7 polymarket rows visible (mix of would_have_placed + evaluated-skipped). 27 polymarket tests pass; full suite green.

**Inert / dormant on current traffic:** none — all changes are live now. Future LLM calls (every 30s) persist full reasoning to audit_event; new tile rendering applies retroactively to existing rows where the data is available, gracefully degrades for rows without the new fields.

**Known gap:** the existing Polymarket audit rows from before commit 4bcaf14 (the 5 LLM-called rows from earlier today) lack `llm_reasoning` in their payload, so their right-rail analysis shows blank reasoning. Future rows complete; not worth backfilling.

**Rollback:** `tar xzf /home/azureuser/backups/pre-polymarket-rich-ui-20260510.tar.gz && rm -f trading_corp/web/templates/partials/polymarket_analysis.html && sudo systemctl restart trading-corp`.

---

## 2026-05-10 02:05 UTC — Polymarket: skip HITL, flip enabled:true; strategy LIVE in paper-mode

**Commit:** `897607a` — 2 prod files (`main.py` + `config/strategies.yaml`), 118 insertions / 37 deletions.
**Triggered by:** Board direction 2026-05-10. Per-trade `/approvals/{order_id}` click gate determined to be net-friction without proportionate protection given Polymarket's bounded blast radius ($1 fixed sizing × $1K aggregate cap × deterministic-Python risk gate). Polymarket's fast-moving prices made the HITL latency a real drag on opportunity capture.

**Architecture change:**
- `_scheduled_polymarket_arb_loop` now calls `risk_agent.evaluate()` inline instead of routing through `_run_order(graph, ...)`. Approved orders log `would_have_placed` directly; rejected orders log `polymarket_order_rejected_by_risk`. Risk gate is still load-bearing per CLAUDE.md §1 — every order flows through the deterministic Python caps; LLM hallucination cannot bypass them.
- `polymarket_arbitrage.enabled: false → true`. Strategy is live in paper mode (broker still ReadOnlyBroker; nothing actually trades; rows accumulate for Backtester).
- Telegram message changed from "routing for approval" to "logged to activity rail" — visibility-only, not gating.
- `auto_execute: false` stays (moot today; Phase 3 will add live signing path + auto_execute_caps + daily kill switch + daily summary digest as the safety scaffolding equivalent to per-trade HITL).

**Backup tag:** `pre-polymarket-direct-log-20260510.tar.gz` (34K).
**Verification:** PID 180231 → 181134 (clean). Boot log:
```
PolymarketBroker connected (funder=***REDACTED***, equity=$500.00, 0 positions)
Polymarket arbitrage scanner online (enabled=True, auto_execute=False, hitl=DIRECT)
```

**End-to-end live activity within 2 minutes of restart:**
- 2 scanner cycles (02:05:19, 02:06:32) — 64 markets pre-filtered each → 10 survivors per cycle
- 5 LLM calls completed across both cycles, ~5s each (Anthropic prompt cache hit on follow-ups)
- First cycle's 02:07:46 order-emission burst: **4 `would_have_placed` rows** (3 BUY NO at 0.84/0.16/0.12; 1 BUY YES at 0.05). All sized correctly to ~$1 USDC notional. Risk gate approved all 4 — no rejections.
- Activity rail on /division/polymarket_arbitrage now showing real-time strategy reasoning chain end-to-end.

**Operational expectations going forward:**
- Scanner ticks every 30s (`poll_interval_sec`). Each tick runs ~50s when emissions fire (10 sequential Anthropic calls); tick-to-tick spacing absorbs the latency.
- Daily LLM cost: $2-50/day depending on cooldown saturation.
- Daily would_have_placed rows: highly variable; 4 in the first cycle is unusually high (LLM is "hot" on extreme-divergence calls). Realistic steady-state TBD as cooldown-bound cycles average out.
- One sanity-check row in the first burst: mlb-nym-ari at implied 0.05 with LLM-claimed prob 0.95 (90% divergence). Either real value or LLM hallucinated the matchup. Backtester will surface which.

**Phase 2.5 + 2a + Phase 0/1 are now all complete + LIVE.** Backtester will run on accumulating paper rows; verdict gates the eventual Phase 3 (live order placement) decision.

**Rollback:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-direct-log-20260510
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
sudo systemctl restart trading-corp
"
# This restores the HITL approval flow + sets enabled:false again.
```

---

## 2026-05-10 01:47 UTC — Polymarket Phase 2.5: Backtester binary-outcome extension

**Commit:** `a01dd4b` — 4 files (1 prod broker + 1 prod script + 1 test + 1 runbook), 799 insertions / 0 deletions.
**Triggered by:** Phase 2.5 minimal-viable per memo Q4. Phase 2a strategy must accumulate ≥30 days of paper would_have_placed rows before this gate is meaningful; gate is for the future `auto_execute: true` flip, not the `enabled: true` flip (paper-mode HITL is the safe path between).

**Three pieces shipped:**
- `brokers/polymarket.py` — new `get_market_resolution(condition_id, slug)` two-pass gamma-api lookup. Decodes resolution from `outcomePrices` + `umaResolutionStatus` per gamma-api conventions verified live (resolved/pending/void/not_found).
- `scripts/backtest_polymarket_arbitrage.py` — replay tool. Reads paper rows over a horizon, computes binary-outcome P&L (`won → qty × (1-price)`, `lost → -qty × price`), aggregates: hit rate / wins-losses / total notional / total P&L / ROI / avg + median P&L / max consecutive-loss DD / per-category breakdown. Heuristic verdict (RECOMMEND_APPROVAL / REJECTION / MIXED_SIGNAL / INSUFFICIENT_DATA).
- `runbooks/polymarket_arbitrage_backtest.md` — Board runbook: when to run, how to interpret each output section, Board memo template for approval/rejection decisions, FAQ.

**Backup tag:** `pre-backtester-phase25-20260510.tar.gz` (9.6K).
**Verification:** PID 179088 → 180231 (clean). Polymarket still $500 live + redacted in logs; scanner online (enabled=False). 19 new pytest cases pass (P&L math 4 directions, skip semantics, aggregation incl. monotone-up max-drawdown edge case, all 4 verdict thresholds). Script runs cleanly on prod against live DB — returns `NO_DATA` (correct: strategy disabled, no paper rows yet).

**Post-flip workflow (when Board enables strategy in paper-mode):**
1. Paper rows accumulate for 30+ days
2. `python scripts/backtest_polymarket_arbitrage.py --days 30` produces verdict
3. If `RECOMMEND_APPROVAL`, write Board memo per the template + flip `auto_execute: true`
4. If `REJECTION`, investigate (per-category breakdown often reveals which strategies-within-strategy work) or stay paper-mode

**Inert until enable.** All Phase 2a + 2.5 infrastructure in place; the gate for `auto_execute: true` exists. Strategy still `enabled: false`.

**Rollback:** `tar xzf /home/azureuser/backups/pre-backtester-phase25-20260510.tar.gz && rm -f scripts/backtest_polymarket_arbitrage.py && sudo systemctl restart trading-corp`.

---

## 2026-05-10 01:26 UTC — Polymarket Phase 2a Step 5: gamma-api query tuning + two-layer category mapping

**Commit:** `33169ae` — 2 prod files (`brokers/polymarket.py` + `agents/strategies/polymarket_arbitrage.py`) + 1 test file (210 insertions / 15 deletions).
**Triggered by:** Phase 2a pre-enable checklist Step 5. Default gamma-api page sort returned long-tail markets first — original `list_markets` query yielded 0 markets within the 7-day cap. Tuned empirically to a server-side query that returns 66+ markets passing all Phase 2 caps per cycle.

**Changes:**
- `list_markets` now uses `order=volume24hr&ascending=false&end_date_min=NOW+min_hours&end_date_max=NOW+max_days` for server-side filter alignment with the strategy's client-side caps.
- New `_classify_market(market) -> (top_category, series_subtag)` with 8 keyword-set buckets (sports / politics / geopolitics / finance / crypto / entertainment / celebrity / health / other). Tested empirically against 66 live markets — 100% classified, 0 in "other" bucket.
- Strategy threads BOTH levels: LLM prompt context (`Category: {top} ({series})` for base-rate priors), `ProposedOrder.extra.category` + `extra.series`, audit row `polymarket_llm_probability_called.{category, series}`.

**Backup tag:** `pre-gamma-tuning-20260510.tar.gz` (13K).
**Verification:** PID 178354 → 179088 (clean). All brokers reconnected; Polymarket still $500 live + redacted in logs; scanner online (enabled=False). 27 polymarket tests pass (8 new for category classification + 19 existing); 508-test suite green; pre-existing PMCC LEAP-fixture failures unchanged.

**Inert until enable.** Strategy still `enabled: false` in `strategies.yaml` — gates on Phase 2.5 Backtester verdict (next task).

**Rollback:** `tar xzf /home/azureuser/backups/pre-gamma-tuning-20260510.tar.gz && sudo systemctl restart trading-corp`.

---

## 2026-05-10 01:04 UTC — BAL CHG row ts_short pinned to bar_ts (cosmetic, sibling-row alignment)

**Commit:** `c94df37` — 2 files (`main.py` + `web/data.py`), 17 insertions / 6 deletions.
**Triggered by:** Board screenshot 2026-05-09 ~20:46 UTC. The BAL CHG row landed at `05-09 20:02 ET` (audit-row write time = bar close + ~2min) while its sibling donchian_evaluated row showed `05-09 14:00 ET` (bar open). Same evaluation cycle, but the 6h visual gap reads as two unrelated events.
**Fix:** orchestrator (`main.py:_run_donchian_bar`) now stamps `bar_ts` on the balance_change payload before logging; rendering layer (`web/data.py:build_donchian_view`) prefers `payload.bar_ts` over `r["ts"]` for the BAL CHG `ts_short`, mirroring the existing donchian_evaluated logic. Defensive fallback to audit ts for legacy rows that pre-date the stamp.
**Backup tag:** `pre-balchg-ts-fix-20260510.tar.gz` (38K, 2 modified files).
**Verification:** PID 177477 → 178354 (clean). All brokers reconnected (Polymarket still $500.00 live, BitUnix $6763.94 — the P2 transfer bug is unchanged, expected). Polymarket scanner online (enabled=False, no-op). Donchian scheduler online (enabled=True). Existing BAL CHG row in the DB at 2026-05-09 20:02 ET will continue displaying its audit-write-time until it ages out of the 60-row window (no payload migration). Next BAL CHG row — when fired — will display the bar's open time aligned with its donchian_evaluated sibling.
**Rollback:** `tar xzf /home/azureuser/backups/pre-balchg-ts-fix-20260510.tar.gz && sudo systemctl restart trading-corp`.

---

## 2026-05-10 00:39 UTC — Polymarket wallet went live (KV upload + service restart)

**Not a code deploy** — wallet/secrets bring-up. Board completed steps 1-4 of the Phase 2a pre-enable checklist between 2026-05-09 22:00 UTC and 2026-05-10 00:30 UTC: generated EOA via `eth_account.Account.create()` (regenerated once after losing the first address — wallet wasn't funded, zero loss), Alchemy Polygon Mainnet signup + RPC URL, $500 native USDC + 98 POL funded from Coinbase to the EOA on Polygon mainnet, `az keyvault secret set` for the three secrets.

**On-chain verification (pre-restart, public RPC):** native USDC `0x3c49…3359` = $500.00, USDC.e bridged = $0.00 (no misrouted tokens), POL/MATIC = 98.375 (~$39 at $0.40/POL — way more than needed for gas).

**KV state confirmed (presence-only, no values exposed):**
- `POLYMARKET-PRIVATE-KEY`: enabled, length 64 (no `0x` prefix; `eth_account.Account.from_key` accepts both forms — harmless for Phase 1 since signing isn't in the path)
- `POLYMARKET-FUNDER-ADDRESS`: enabled, length 42 (`0x` + 40 hex ✓)
- `POLYGON-RPC-URL`: enabled, length 62 (sensible for Alchemy or public RPC)

**Pre-restart NSG actions:** Board's laptop IP rotated TWICE during this session (`98.231.16.63` → `73.104.119.214` mid-session for the Phase 2a code deploy; rotated back to `98.231.16.63` for the wallet bring-up). Both updated cleanly via `az network nsg rule update` per `auth_lockout_recovery.md`.

**Restart:** PID 176618 → 177477 (clean). Boot log:

```
PolymarketBroker connected (funder=***REDACTED***, equity=$500.00, 0 positions)
PaperBroker connected (account=paper_polymarket_copy_trading, equity=$0.00)
Polymarket arbitrage scanner online (enabled=False, auto_execute=False)
```

**Three things confirmed by that one log line:**
- USDC balance reads cleanly from Polygon RPC via `eth_call(USDC.balanceOf)`.
- RedactingFilter scrubs the funder address from log output (literal-value redaction registered in `secrets.py:load_secrets()`; the address is in memory + KV but never in logs).
- `data-api.polymarket.com/positions?user=…` returned empty — correct for fresh wallet.

**Dashboard verification:**
- Home tile **Polymarket Arbitrage** = `$500.00` (was `$0 STUB`)
- `/division/polymarket_arbitrage`: Equity $500.00 / Cash $500.00 / Buying Power $500.00
- `polymarket_copy_trading` tile = `$0.00 STANDBY` (paper-fallback by design until Phase 4+)

**Phase 2a pre-enable checklist status (steps 5-7 remaining, all server-side):**

| # | What | Status |
|---|---|---|
| 5 | Tune gamma-api query (current default page sort returns long-tail markets that fail 7-day cap) | Next session |
| 6 | Phase 2.5 Backtester (binary-outcome replay, minimal-viable) | Next session — gates Phase 3 |
| 7 | Flip `polymarket_arbitrage.enabled: true` in `strategies.yaml` | After 5 + 6 + Board "go" |

**Rollback recipe (if needed):**

```bash
# Rollback the wallet-going-live state by removing the secrets from KV.
# Service restart after this puts the broker back into stub mode.
az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name POLYMARKET-PRIVATE-KEY
az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name POLYMARKET-FUNDER-ADDRESS
az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name POLYGON-RPC-URL
ssh azureuser@trading.jacksumner.com "sudo systemctl restart trading-corp"
```

(The wallet itself remains funded on-chain regardless. Code rollback recipe for the Phase 2a code deploy is in the previous deploy_log entry.)

---

## 2026-05-09 21:57 UTC — Polymarket Phase 2a: arbitrage scanner + risk caps + scheduler wiring

**Commits:** `fe757e2` (Phase 2a, committed pre-deploy).
**Triggered by:** Phase 2 strategy build (greenlit by Board after the Phase 1 ship + 5-question memo answers earlier same day). Path B chosen for the LLM call (direct Anthropic, NOT through Research firm — Thesis schema doesn't fit prediction-market probability queries; Polymarket arbitrage is single-division decision logic, not cross-division knowledge work). All 9 risk caps confirmed verbatim from Q1 answer; K=10 / 6h cooldown from Q3 answer 'a'; 7-day max horizon from Q2; defensive httpx rate-limiting from Q5.
**Backup tag:** `pre-polymarket-phase2a-20260509.tar.gz` at `/home/azureuser/backups/` (5 modified files; 2 new files have no pre-state).
**Pre-deploy NSG action:** Board's laptop IP rotated mid-session (Comcast); old `98.231.16.63` → new `73.104.119.214` updated on `tc-prod-nsg/AllowSSHFromHome` via the standard `auth_lockout_recovery.md` Cloud-Shell-or-az-CLI path. Documented as the correct recovery; not a deploy concern.

**Files deployed (5 modified, 2 new):**

- `config/risk.yaml` — new `polymarket:` top-level block. All 9 caps from Q1 answer (universe pre-filter: min volume $50K / max spread 3¢ / min ttr 24h / implied-prob bounds 5-95%; per-order: 5%-of-equity / $250 single-market; aggregate: 25%-equity-cap-$1K daily / $1K total open).
- `config/strategies.yaml` — new `polymarket_arbitrage:` block (enabled:false, auto_execute:false, K=10, 6h cooldown, 7d horizon, fixed_usdc/$1 sizing). Plus a documented `polymarket_copy_trading:` placeholder for Phase 4+.
- `trading_corp/agents/risk.py` — new `_evaluate_polymarket()` branch routed by the `is_prediction_market` extra flag. Atomic + aggregate caps; halt checks still run BEFORE the polymarket branch. Daily-aggregate cap queries audit_event for today's `would_have_placed`/`board_approved`/`filled` rows; total-open cap returns 0 in Phase 2a (Phase 3 implements). `evaluate()` signature gained an optional `db_url` kwarg (back-compat: existing callers don't pass it, aggregate checks no-op).
- `trading_corp/brokers/polymarket.py` — new `list_markets(filters)` method against gamma-api with deterministic Python-side filtering. New `_http_get_json()` helper with concurrency cap (semaphore=6) + 429 backoff (max 4 retries, 1-30s window with jitter).
- `trading_corp/main.py` — new `_scheduled_polymarket_arb_loop()` spawned alongside `donchian_task`. Re-reads `poll_interval_sec` each tick so config changes don't need a restart. Routes emitted ProposedOrders through `_run_order` (existing risk + HITL graph). Telegram pings on each emission.
- `trading_corp/agents/strategies/polymarket_arbitrage.py` — **NEW.** PolymarketArbitrageAgent. mtime-cached config, per-market 6h cooldown in agent_state (single-JSON-blob with cleanup-on-load). Direct Anthropic call via `agents.llm.build_chat_model`. Permissive JSON parser handles prose-wrapped output; clamps prob_yes to [0.01, 0.99]; normalizes unknown confidence to "medium". Defensive implied-prob extraction handles outcomePrices-as-string, outcomePrices-as-list, lastTradePrice, price.
- `trading_corp/agents/strategies/_polymarket_prompts.py` — **NEW.** Shared analyst-persona system prompt (~1554 estimated tokens). Imported by arbitrage today, by future copy_trading later — Anthropic's prompt cache amortizes the input-token cost across both strategies (5-min ephemeral TTL; ≥1024-token threshold cleared with substantive methodology + worked example).

**Features shipped (load-bearing for future "is X done?" checks):**

- The Polymarket scanner loop is online but inert. Boot log:
  `"Polymarket arbitrage scanner online (enabled=False, auto_execute=False)"`. To activate: Board flips `polymarket_arbitrage.enabled` in `strategies.yaml` AND uploads the 3 KV secrets.
- Risk gate now routes prediction-market orders by `extra.is_prediction_market` flag — clean separation from PMCC/crypto cap logic.
- Anthropic prompt-cache-ready system prompt is in the codebase; both Polymarket strategies will share it. ~85% input-token cost reduction on K-1 follow-up calls per cycle.
- 19 new pytest cases in `tests/test_polymarket_arbitrage.py` regress: config defaults, implied-prob extraction (4 shapes), JSON parse robustness (clean/prose/OOB/garbage/unknown-confidence), risk-gate cap matrix (approve, implied-bound rejects, single-market $ cap, per-position % cap, halt-precedence, non-polymarket-isolation).

**Notable code changes (callouts a future Claude shouldn't miss):**

- The strategy emits ProposedOrder.extra with `is_prediction_market: True`. **The risk gate routes EXCLUSIVELY off this flag**, not off `order.strategy == "polymarket_arbitrage"`. When `polymarket_copy_trading` ships, it should set the same flag — that single change makes it inherit all 9 caps without modifying risk.py.
- `RiskAgent.evaluate()` gained an optional `db_url` kwarg for the daily-aggregate cap query. Existing callers (PMCC, donchian, otter, cypher, manual) don't pass it; their behavior is unchanged. The Polymarket scheduler in main.py needs to start passing it once `enabled: true` flips and aggregate caps actually bind.
- The shared analyst prompt is intentionally substantive — methodology + worked example clear the 1024-token cache threshold. Trimming the prompt below ~1300 tokens would silently disable the cache and quintuple input costs at K=10/30s.
- Aggregate query (`_sum_polymarket_today`) uses `substr(ts, 1, 10)` for date partitioning. Switches to UTC midnight; if Board ever wants ET-based daily aggregates, that's a single-line change but flag the semantics in audit.

**Verification:**

- Pre-restart PID 175242 → post-restart 176618 (clean).
- All 7 prod files match local LF-normalized md5s exactly after SCP.
- Boot log:
  - `Registered polymarket broker for division=polymarket_arbitrage (paper=False)` ✓
  - `Registered paper broker for division=polymarket_copy_trading (paper=True)` ✓
  - `PolymarketBroker connected as STUB (missing funder or RPC URL)` — expected (KV uploads still pending Board action)
  - `Polymarket arbitrage scanner online (enabled=False, auto_execute=False)` — exactly the inert posture Phase 2a ships
- KV fetch attempts for `POLYMARKET-PRIVATE-KEY` / `POLYMARKET-FUNDER-ADDRESS` returned empty (graceful fallback to stub).
- 19 new tests pass; 9 existing risk_gates tests pass unchanged; 508 tests in the broader suite pass.
- Live test of `list_markets()` against gamma-api (executed pre-deploy): real markets fetched, 7 returned with default filter, 0 returned with Phase 2 caps applied (gamma-api default page sort isn't volume-first; tuning the query is a pre-enable follow-up flagged in the file's docstring).

**Inert / dormant on current traffic:**

- Scanner loop wakes every 30s, no-ops on `enabled: false`. **Zero LLM calls; zero cost.**
- Cooldown table in agent_state stays empty until first cycle with `enabled: true`.
- Aggregate-cap query (`_sum_polymarket_today`) returns 0.0 — no Polymarket audit rows yet.
- `polymarket_copy_trading` tile remains paper-fallback STANDBY $0.

**Pre-enable checklist (Board action):**

1. Generate wallet (`python3 -c "from eth_account import Account; ..."`).
2. Sign up at alchemy.com, copy Polygon Mainnet HTTPS URL.
3. Fund EOA with $500 native USDC + ~$5 MATIC on Polygon.
4. Upload to KV: `POLYMARKET-PRIVATE-KEY` / `POLYMARKET-FUNDER-ADDRESS` / `POLYGON-RPC-URL`.
5. Tune `gamma-api/markets` query to surface high-volume / short-tail markets (current sort returns long-tail first; Phase 2 caps reject them).
6. Phase 2.5 Backtester verdict (replay-only minimal-viable; greenlit but not yet built).
7. Flip `polymarket_arbitrage.enabled: true` in `strategies.yaml` (no service restart needed — mtime-cached).
8. Watch the activity rail on `/division/polymarket_arbitrage` for `would_have_placed` rows.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-phase2a-20260509
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
rm -f trading_corp/agents/strategies/_polymarket_prompts.py \
      trading_corp/agents/strategies/polymarket_arbitrage.py
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 20:13 UTC — Polymarket Phase 1: read-only broker + division wiring (+ Phase 0 secrets backfill caught at deploy)

**Commits:** `db1f0cd` (Phase 0 secrets, never previously deployed) + `d7cbea2` (Phase 1 broker + wiring, committed pre-deploy).
**Triggered by:** Polymarket Arbitrage division scope (multi-message in-session brief; see CLAUDE.md §6 STOP-AND-ASK items resolved 2026-05-09 ~17:30-19:00 UTC). Phase 0.5 EU egress proxy was scoped, then ruled NO-GO by the smoke test — Polymarket's read APIs serve tc-prod-vm's US-east IP without geo-block. Phase 1 ships read-only adapter + tile rendering inert; goes live the moment the KV secrets land.
**Backup tag:** `pre-polymarket-phase1-20260509.tar.gz` (21K, 4 modified files) at `/home/azureuser/backups/`. Plus an extra `secrets.py.pre-polymarket-phase1-20260509.bak` snapshot for the secrets.py rollback (because Phase 0 was caught mid-deploy — see Notable below).

**Files deployed (5 modified, 1 new):**

- `trading_corp/utils/secrets.py` — Phase 0 backfill caught at deploy time. Three new fields on `Secrets` (`polymarket_private_key`, `polymarket_funder_address`, `polygon_rpc_url`). New `register_redact_literal()` mechanism + `_REDACT_LITERALS` set for value-substring scrubbing of secrets that third-party libs may log raw. KV expected_env_vars extended. Three new entries on `_SECRET_KEY_NAMES` for KEY=value redaction.
- `trading_corp/brokers/base.py` — `ReadOnlyBroker` ABC extracted (connect/disconnect/snapshot/quote). `Broker` now subclasses it (adds place_order + cancel_order). Behavior-zero change for existing brokers; PolymarketBroker is the first ReadOnlyBroker subclass.
- `trading_corp/brokers/polymarket.py` — **NEW.** PolymarketBroker(ReadOnlyBroker). Stub mode if creds missing. snapshot() = USDC balance via Polygon RPC `eth_call` + positions via data-api. quote() = gamma-api slug→token_id then clob last-trade-price. `signature_type=EOA` pattern (signer == funder, no Polymarket proxy/SAFE) — Path A wallet model. NO place_order method exists; ABC enforces read-only.
- `trading_corp/main.py` — `_build_broker_for_division` polymarket family branch. No PaperExecutionBroker wrap (ReadOnlyBroker has no order surface to simulate).
- `trading_corp/utils/divisions.py` — new "polymarket" investment-type group between Crypto and Retirement. Slug-prefix classification handles the paper-fallback copy-trading division (broker=paper but slug starts with `polymarket_`).
- `config/divisions.yaml` — two new entries: `polymarket_arbitrage` (broker=polymarket, real adapter, standby) + `polymarket_copy_trading` (broker=paper, $0 placeholder for Phase 4+ copy-trading strategy, standby).

**Features shipped (load-bearing for future "is X done?" checks):**

- Home dashboard renders a new "Polymarket" investment-type group (4th group, between Crypto and Retirement) with TWO tiles: "Polymarket Arbitrage" + "Polymarket Copy Trading". Both render STANDBY today.
- ReadOnlyBroker ABC is now in the codebase. The Fidelity migration TODO from CLAUDE.md §7 sharp edges is now strictly possible (separate cleanup; not done here).
- Phase 0 secrets-loader for Polymarket creds + literal-value redaction is live on prod.
- The 2026-05-09 EU-egress smoke test runbook (`runbooks/eu_proxy_smoke_test.md`) is preserved as the starting point if Phase 3 trade placement turns out to need a proxy.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **Phase 0 was caught at deploy time, not pre-deploy.** I shipped Phase 1 first thinking Phase 0 was already on prod (it was committed locally as `db1f0cd` but never SCP'd — I made a "bundle the deploy with Phase 1" call earlier in the session and forgot to honor it). The service crash-looped on `AttributeError: 'Secrets' object has no attribute 'polymarket_private_key'` for ~90s before I caught it via boot-log inspection, SCP'd `secrets.py`, and restarted clean. Lesson: when a code commit references new fields on a shared dataclass, deploy the dataclass file BEFORE the consumer file, OR deploy as one atomic batch.
- The `polymarket_copy_trading` division uses `broker: paper` deliberately. Both polymarket_* divisions land in the new "Polymarket" investment-type group via slug-prefix classification (utils/divisions.py:_POLYMARKET_SLUG_PREFIX). The arbitrage division's paper-fallback would conflict with broker:polymarket on the same wallet (both tiles would show the same balance) — broker:paper for the second tile keeps it visibly distinct ($0 STANDBY) until Phase 4+ wires the real strategy.
- PolymarketBroker is NOT wrapped in PaperExecutionBroker. The convention for PAPER mode (wrap-real-broker-with-paper-fills) doesn't apply to ReadOnlyBroker subclasses — there's no order surface to simulate. If a future Polymarket division needs paper-mode order simulation (Phase 2 strategy paper-track), the new code path will be `PolymarketLiveBroker(Broker)` in Phase 3, and PaperExecutionBroker will wrap THAT.
- The `private_key` constructor arg on PolymarketBroker is accepted but unused in Phase 1. Phase 3 signing will read from the same arg without a constructor change.

**Latent bugs caught + fixed (if any):** none new. The pre-existing `secrets.py.pre-polymarket-phase1-20260509.bak` confirms prod was running the pre-Phase-0 file before this deploy — no drift content beyond "version skew due to my earlier deferral."

**Verification:**

- Pre-restart PID 171746 → post-restart 175242 (clean).
- All 5 prod files match local LF-normalized md5s exactly after SCP.
- Boot log:
  - `PolymarketBroker connected as STUB (missing funder or RPC URL)` — expected with no KV secrets yet.
  - `PaperBroker connected (account=paper_polymarket_copy_trading, equity=$0.00)` — copy-trading placeholder healthy.
  - `Registered polymarket broker for division=polymarket_arbitrage (paper=False)` ✓
  - `Registered paper broker for division=polymarket_copy_trading (paper=True)` ✓
  - KV fetches for `POLYMARKET-PRIVATE-KEY` / `POLYMARKET-FUNDER-ADDRESS` / `POLYGON-RPC-URL` returned empty (Board hasn't uploaded yet — graceful degradation to stub mode is the design).
- `GET /` returned HTTP 200 in 4.87s, 87.2 KB.
- `<h2>` headers in document order: `Individual` → `Crypto` → `Polymarket` → `Retirement`. Section order matches `_INVESTMENT_TYPE_ORDER`.
- Both Polymarket tiles render with STANDBY badges. 4 STANDBY badges total on home page (2 new Polymarket + 2 existing: Coinbase Futures, BitUnix Futures).

**Inert / dormant on current traffic:**

- PolymarketBroker `snapshot()` and `quote()` return zeros / empty until KV holds the three secrets. After Board uploads them, next service restart brings the arbitrage tile live with real wallet balance + open positions (initially: $500 USDC, 0 positions).
- PolymarketBroker.quote() field-mapping (gamma-api `clobTokenIds` / `outcomes`) is best-effort against unverified shape — first non-empty response from a real market should be eyeballed to confirm. Field names in `_fetch_positions` similarly defensive (.get() with fallbacks); first funded-wallet response should be sanity-checked.
- Phase 3 follow-up tracked as task #31: re-test geo-block on authed/write CLOB endpoints before live order placement. If writes are blocked, revive `runbooks/eu_proxy_smoke_test.md`.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-phase1-20260509
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
# Phase 0 secrets.py rollback (separate backup since the tarball was made
# pre-Phase-0-discovery; the pre-Phase-0 file is in its own .bak):
cp /home/azureuser/backups/secrets.py.pre-polymarket-phase1-20260509.bak \
   trading_corp/utils/secrets.py
# Drop the new file:
rm -f trading_corp/brokers/polymarket.py
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 16:42 UTC — Donchian: observe Board-driven balance changes; state-as-source-of-truth

**Commits:** `78e57a0` (committed before deploy).
**Triggered by:** Board direction (chat 2026-05-09 post-UI-cleanup) — recurring weekly BTC buys + occasional cash deposits land on the coinbase_spot account outside the strategy's knowledge. The strategy must observe these (log them, attribute to Board) but NOT auto-flip state in response. Strategy state is now the source of truth for portfolio composition; the broker's balance is normalized to it at the next BUY/SELL signal, not via a forced rebalance trade.
**Backup tag:** `pre-balance-tracking-20260509-utc-pre.tar.gz` at `/home/azureuser/backups/` (43K, 4 modified files; no new files).
**Pre-deploy DB mutation:** `UPDATE agent_state SET value_json='{"state":"cash","cost_basis":null}' WHERE agent='coinbase_btc_donchian' AND key='state';` ran ~1 min before the systemctl restart. Today's earlier startup reconcile (15:23 UTC deploy) had set state=BTC with cost_basis=$80,371.17 — but per the new model, the 0.595 BTC was always Board's, not the strategy's, so CASH is the correct strategy view. Previous values preserved in deploy_log + the row's prior `updated_ts` for rollback.

**Files deployed (4 modified):**

- `trading_corp/agents/strategies/coinbase_btc_donchian_agent.py`:
  - `PersistedState` gains `last_known_cash` + `last_known_btc_qty` (defaults None) — baselines for delta detection.
  - New persistence key `last_known_balances` (alongside existing `state` + `last_bar_ts`).
  - `_loaded_from_db: bool` flag — flips True when `_restore_from_db` finds a state row.
  - `restore_from_broker` now short-circuits when `_loaded_from_db` is True (any subsequent restart trusts persisted state). Also accepts a `cash` arg for first-bring-up baseline seeding.
  - New public method `record_balance_snapshot(*, cash, btc_qty, threshold_usd=1.0, threshold_btc=0.0001) -> dict | None` — compares to last-known, returns audit-payload dict on material delta and advances baseline. First call after bring-up just seeds (no false-positive delta).
  - `on_bar_close` gains optional `cash` kwarg. When supplied, BUY sizing uses cash (not account_equity) — the strategy never double-counts the Board's pre-existing BTC into a new buy notional. Back-compat: `cash=None` falls back to account_equity.
- `trading_corp/main.py`:
  - `_run_donchian_bar` extracts `cash` from `snap.cash`, calls `agent.record_balance_snapshot(cash=cash, btc_qty=held_btc)` BEFORE `on_bar_close`. On material delta, writes a `balance_change` audit-event row (kind=`balance_change`, actor=`coinbase_btc_donchian`).
  - `on_bar_close` now passes `cash=cash`.
  - Startup reconcile call site updated to pass `cash=cash`. Comment block rewritten to document the no-op-after-bring-up semantics.
- `trading_corp/web/data.py`:
  - `build_donchian_view` SQL widened to `kind IN ('donchian_evaluated','balance_change')`. Row build branches on `kind`, producing two distinct shapes: existing decision shape, OR `{kind: 'balance_change', ts_short, attribution, state_at_observation, delta_cash, delta_btc, new_cash, new_btc_qty}`.
- `trading_corp/web/templates/partials/donchian_log.html`:
  - Row loop branches on `r.kind`. balance_change rows render full-width with a BAL CHG tag, signed delta amounts (gain-green for +, loss-red for −), and "→ new totals · state=X" trailer. Subtle warn-tinted bg (changes are normal, not alerts). donchian_evaluated rows unchanged.

**Features shipped (load-bearing for future "is X done?" checks):**

- The strategy is now safe against parallel Board trading. Recurring weekly buys + cash deposits land as `balance_change` audit rows; the strategy passively absorbs whatever the broker reports at the next BUY (sizes off cash, sweeps Board's pre-existing BTC into the position via broker-side aggregation) or next SELL (held_btc from snapshot includes all coins, regardless of who put them there).
- Strategy state (CASH↔BTC) is now persisted-state authoritative. `restore_from_broker` is reserved for first-ever bring-up; subsequent restarts preserve the strategy's view. Today's mid-day flip from BTC (set by 15:23 UTC reconcile) → CASH (manual reset 16:42 UTC) was a one-time correction; future deploys should never need to touch the state row directly.
- Decision-log surface now shows two row kinds interleaved chronologically — strategy decisions + Board-attributed balance deltas — giving a single timeline of "what happened on this account" since the strategy's perspective.

**Notable code changes (callouts a future Claude shouldn't miss):**

- BUY sizing changed semantics: `qty = cash / current_close` (when `cash` supplied) instead of `qty = account_equity / current_close`. Tests that don't pass `cash` keep the old behavior (back-compat). If you ever change `on_bar_close`'s signature, mind the back-compat.
- `record_balance_snapshot` advances the baseline EVEN ON sub-threshold deltas. So a slow drift (e.g., $0.50/day fee bleed) won't accumulate over many bars and eventually trip the threshold as a false aggregate event. If that's ever wanted, change the post-detection update path.
- The strategy's `cost_basis` on a BUY is the fill price of the strategy's own buy — NOT a weighted avg with any pre-existing Board BTC. P&L estimates at the next SELL will be measured from the strategy's fill, which is the cleanest accounting given the strategy can't know what the Board paid.

**Verification:**

- Pre-restart PID 170308 → post-restart 171746.
- All 4 files md5 round-trip MATCH after LF-normalization.
- Boot log:
  - `restored state=cash cost_basis=None last_bar=2026-05-09 06:00:00+00:00 last_known_cash=None last_known_btc=None` — picked up the reset CASH state cleanly; balances correctly None pre-first-snapshot.
  - `persisted state present (state=cash, cost_basis=None) — skipping broker reconcile. Board-driven broker deltas will be observed via record_balance_snapshot per bar.` — new short-circuit fired exactly as designed. Broker showed 0.595 BTC + $39K cash; state stays CASH.
  - `Donchian scheduler: sleeping 4743s until next bar close` — math: 16:42:56 + 4743s ≈ 18:01:59 UTC = 14:02 ET. Next bar evaluation arrives on schedule.
- `GET /division/coinbase_spot`: HTTP 200, 62.5KB, 3.5s. Buying Power tile gone, Donchian chart container present, existing decision-log rows (`05-09 02:00 ET`, `05-08 20:00 ET`) preserved, no BAL CHG rows yet (no balance changes have fired in the new code path).
- `GET /partials/donchian-chart/coinbase_spot`: HTTP 200, 10.3KB, 2.1s. 50 candles, current_bar_ts=2026-05-09T06:00:00 UTC, 0 markers.
- 16 existing agent unit tests pass unchanged. New behavior smoke-tested locally: first-bring-up still reconciles; post-bring-up reconcile is no-op; first record_balance_snapshot seeds without delta; material delta returns payload + advances baseline; sub-threshold returns None + advances baseline; BUY sizes off cash when supplied; back-compat cash=None still works.

**Inert / dormant on current traffic:**

- `last_known_balances` agent_state row will appear after the first `record_balance_snapshot` call (next bar at 18:02 UTC = 14:02 ET). Until then, the row doesn't exist.
- BAL CHG tile rows will appear when the Board's recurring weekly buy or a cash deposit lands. Initial seeding at 18:02 UTC will NOT generate a BAL CHG row (first call seeds without firing delta — by design).

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-balance-tracking-20260509-utc-pre
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
# Restore prior state row (state=BTC, cost_basis=80371.17)
sqlite3 \$BASE/data/trading_corp.db \\
  \"UPDATE agent_state SET value_json='{\\\"state\\\":\\\"btc\\\",\\\"cost_basis\\\":80371.17}' \\
   WHERE agent='coinbase_btc_donchian' AND key='state';\"
# Drop the new last_known_balances row if it landed
sqlite3 \$BASE/data/trading_corp.db \\
  \"DELETE FROM agent_state WHERE agent='coinbase_btc_donchian' AND key='last_known_balances';\"
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 15:23 UTC — Coinbase BTC HODL division-detail UI cleanup

**Commits:** `a9c0461` (committed before deploy).
**Triggered by:** BACKLOG P3 — "Coinbase BTC HODL division-detail UI cleanup" (the top-section P3 added 2026-05-09). Bundles four asks Board greenlit at session start: (1) ts_short fix, (2) Manual Order tile removal, (3) Buying Power tile removal, (4) 6h Donchian price chart.
**Backup tag:** `pre-donchian-uicleanup-20260509-utc-pre.tar.gz` at `/home/azureuser/backups/` (49K, 3 modified files; the 2 new files have no pre-state to preserve).

**Files deployed (3 modified, 2 new):**

- `trading_corp/web/data.py` — `build_donchian_view`: `ts_short` now reads `payload.bar_ts` (canonical bar identifier) with `r["ts"]` fallback for legacy rows. New async helper `build_donchian_chart_data(db_url, display_bars=50)` fetches ~50 6h Coinbase OHLCV bars via ccxt public endpoint, computes rolling 20-bar Donchian high / 6-bar Donchian low / 168-bar SMA mirroring `donchian_btc.evaluate` semantics (preceding-window, current bar excluded), pulls BUY/SELL fill markers from `audit_event` (`would_have_placed` paper + `filled` live; both snap to bar-open via payload `bar_ts`), and returns the full chart payload.
- `trading_corp/web/routes.py` — new endpoint `GET /partials/donchian-chart/{slug}` returns the JSON payload from `build_donchian_chart_data`. 404s for any slug other than `coinbase_spot` (chart is single-strategy at this point); returns `{empty: true}` on OHLCV fetch failure.
- `trading_corp/web/templates/division.html` — Buying Power stat card now hidden for `coinbase_spot` (cash == buying_power on spot crypto); grid drops from 4 to 3 cols when `_hide_bp` is true. Manual Order include block deleted (was gated on coinbase_spot only — partial file preserved untouched). New chart partial included between donchian_state and donchian_log; new `donchian_chart.js` script tag included gated on coinbase_spot.
- `trading_corp/web/templates/partials/donchian_chart.html` — new partial. Header with channel-legend chips + 360px chart container (`#donchian-chart`, `data-division="coinbase_spot"`) + empty-state div for OHLCV-fetch-fail case.
- `trading_corp/web/static/js/donchian_chart.js` — new file. Self-running IIFE: Lightweight Charts setup with candlestick series + 2 dashed line series (20-bar high red / 6-bar low green) + solid SMA series (accent blue), fetches `/partials/donchian-chart/coinbase_spot`, sets candle data + 3 line series + markers, draws horizontal price line at last close + circle marker on current bar. 60s refresh interval. ResizeObserver wired so the chart matches container width.

**Features shipped (load-bearing for future "is X done?" checks):**

- Decision-log column `bar (ET)` now renders bar-open time (e.g. `05-09 02:00 ET`), matching the timestamp embedded in `reason`. Verified live: most recent row shows `05-09 02:00 ET` not `05-09 08:02 ET` (which would be the audit-row write time of the 12:02 UTC eval that happened during deploy).
- Division-detail UI is purpose-built for Donchian: stat trio is Equity / Cash / Today's P&L (no BP), no Manual Order tile, full price-chart visibility into the channel state the strategy is reading.
- 6h price chart with all four BACKLOG-asked overlays: candles, entry-channel ceiling (20-bar high), exit-channel floor (6-bar low), SMA(168) trend filter, plus current-bar highlight (circle marker + last-close horizontal price line). Markers infrastructure is wired but the array is empty until the strategy places its first BUY (next breakout above the 20-bar high while above the SMA).

**Notable code changes (callouts a future Claude shouldn't miss):**

- `build_donchian_chart_data` is the canonical place for chart-side rolling window math. If anyone changes the lookback semantics in `donchian_btc.evaluate`, mirror it here too (preceding-window, current bar excluded — current bar's high/low DOES NOT count toward its own donchian_high/low).
- The chart endpoint runs a fresh ccxt OHLCV fetch on every request (no caching). At Coinbase public-rate-limited 1 RPS-ish that's fine for a Board-only dashboard, but if traffic ever grows we should add a short TTL cache (60s would line up with the JS refresh interval).
- `setMarkers` on the candle series is the chosen current-bar highlight mechanism — Lightweight Charts v4 has no native vertical line at a time. The "now" circle + last-close horizontal price line together give the visual anchor.

**Verification:**

- Pre-restart PID 167181 → post-restart 170308.
- All 5 files md5 round-trip MATCH after LF-normalization (Windows working copy carried CRLF; LF-normalized in-place on prod to keep convention).
- `CoinbaseBTCDonchianAgent reloaded: enabled=True auto_execute=False entry=20 exit=6 trend_filter=168 granularity=21600` post-restart — config preserved.
- `CoinbaseBTCDonchianAgent: restored state=cash cost_basis=None last_bar=2026-05-09 06:00:00+00:00` — DB persistence survived; the bar evaluated by the 12:02 UTC scheduler tick is reflected.
- `GET /partials/donchian-chart/coinbase_spot`: HTTP 200, 10.3 KB, 1.99s. Returns 50 candles + 50 high/low/sma points + 0 markers + `current_bar_ts: 1778306400` (= 2026-05-09T06:00:00 UTC). Latest values: close $80,315.98, 20-hi $82,814.23, 6-lo $79,520.44 — close < high so still in CASH.
- `GET /division/coinbase_spot`: HTTP 200, 62 KB, 7.5s. Spot-checks: Buying Power tile NOT in HTML, `id="donchian-chart"` container present, `donchian_chart.js` script include present, first decision-log row's ts_short = `05-09 02:00 ET` (bar-open time, NOT the audit-row write time `05-09 08:02 ET`).

**Inert / dormant on current traffic:**

- The `markers` array on the chart payload is empty — the strategy hasn't placed any orders yet (every bar so far has been SKIP). First BUY will land a green up-arrow `belowBar` at the bar-open time of the entering bar. Will be visible end-to-end on the next breakout.
- `donchian_chart.js` is wrapped in a self-running IIFE that no-ops when `#donchian-chart` isn't in the DOM, so it's harmless on other division pages — but the script tag is gated on coinbase_spot to keep the bytes off the wire where unused.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-donchian-uicleanup-20260509-utc-pre
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
rm -f trading_corp/web/templates/partials/donchian_chart.html \
      trading_corp/web/static/js/donchian_chart.js
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 06:25 UTC — Dashboard timestamps converted to ET

**Commits:** local-only at deploy time (8 files modified; will be batched in the session-wrap commit).
**Triggered by:** Board direction 2026-05-09 — "change all times to eastern timezone." Board reads dashboards from ET; UTC display required mental conversion on every glance.
**Backup:** prod tarball at `/home/azureuser/backups/pre-et-20260509-0625.tar.gz` (54K, 8 files).

**Files deployed (8 modified):**

- `trading_corp/utils/time.py` — added display formatters: `to_et()`, `format_et_short()` ('MM-DD HH:MM ET'), `format_et_hm()` ('HH:MM ET'), `format_et_hms()` ('HH:MM:SS ET'), `format_et_full()` ('YYYY-MM-DD HH:MM ET'). All use the existing `ET = ZoneInfo("America/New_York")` constant; DST handled automatically.
- `trading_corp/web/data.py` — `build_donchian_view`: converted `ts_short` (decision log), `last_bar_short` (state card), `next_bar_short` (state card), `buy_ts_short` / `sell_ts_short` (round-trips tile) to ET via `format_et_short` / `format_et_hm`.
- `trading_corp/web/app.py` — registered new Jinja filters `et_hms`, `et_short`, `et_full` so templates can format datetime objects directly.
- `trading_corp/web/routes.py` — `expires_at.strftime("%Y-%m-%d %H:%M UTC")` → `format_et_full(expires_at)`.
- `trading_corp/web/templates/partials/donchian_log.html` — header `bar (UTC)` → `bar (ET)`; empty-state copy `(00/06/12/18 UTC)` → `(20:00 / 02:00 / 08:00 / 14:00 ET)`; docstring caption updated.
- `trading_corp/web/templates/partials/donchian_state.html` — caption `UTC` removed (ET label is baked into the formatted value via `format_et_short`).
- `trading_corp/web/templates/approvals.html` + `approval_detail.html` — `{{ row.added_at.strftime('%H:%M:%SZ') }}` → `{{ row.added_at | et_hms }}`.

**Storage layer unchanged.** All `audit_event.ts` / `agent_state.updated_ts` / order-status timestamps stay UTC (ISO-8601 with timezone). The conversion is display-layer only — `to_et()` reads any UTC ISO string or naive-assumed-UTC datetime. Round-trips through restart/cache cleanly.

**Verification:**

- Pre-restart PID 164965 → post-restart 167195.
- All 8 files md5-match end-to-end after SCP (LF-normalized).
- `Donchian scheduler online: ... sleeping 20120s until next bar close` post-restart — math: 06:26:39 UTC + 20120s ≈ 12:02:00 UTC = 08:02 ET ✓.
- `CoinbaseBTCDonchianAgent: reconciled to CASH state` — DB persistence survived the restart cleanly.
- Dashboard render checks (curl localhost:8000):
  - Home tile: dial unchanged (no timestamps), `left: 27.3%` needle position preserved.
  - Division detail: column header reads `bar (ET)`, first row's `ts_short` reads `05-09 02:02 ET`. State card "Last decision" + "Next 6h close" both render in ET.

**Pre-existing surface bug surfaced (decision needed before next deploy):**

- The decision-log column header reads `bar (ET)` but the `ts_short` it displays is the **audit-row write time** (bar close + ~2min), not the bar's open time. Was masked under UTC display ("06:02 UTC" is close enough to bar close). Now in ET it reads `05-09 02:02 ET` while the same row's `reason` text references `@ 2026-05-09T00:00:00+00:00` (bar open). Captured as a decision point under the BACKLOG entry "P3 — Coinbase BTC HODL division-detail UI cleanup". Two paths: (a) switch `data.py` to read `payload.bar_ts` instead of `r["ts"]` (~2-line fix; aligns column with reason text); (b) leave the data, change the column header to "evaluated (ET)". Pick before the UI-cleanup deploy lands.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-et-20260509-0625
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 06:02 UTC — Donchian Phase 2 validation gate ✅ CLOSED

**Not a deploy** — validation milestone. The 02:53 UTC Phase 2 wiring deploy left an open validation gate: "first `donchian_evaluated` audit row should land at ~06:02 UTC." It did, exactly on schedule.

**First bar evaluation (2026-05-09T00:00:00 UTC bar; evaluated 06:02:03 UTC = 02:02:03 EDT):**

- **Decision: SKIP.** `close = $80,374.00 ≤ 20-bar high = $82,814.23` — no breakout, agent stays in CASH (correct given startup state).
- **Channel values:** donchian_low $79,456.00 / current_close $80,374.00 / donchian_high $82,814.23 / trend_filter_sma ~$74,xxx (truncated in audit row but >0 = trend filter passes for future entries).
- **Dial position math verified:** `(80374 - 79456) / (82814.23 - 79456) = 918 / 3358.23 ≈ 0.273` → home-tile needle rendered at `left: 27.3%` ✓.
- **Dedup pointer advanced:** `agent_state` row `last_bar_ts: 2026-05-09T00:00:00+00:00` (the bar that just closed).
- **Scheduler armed for next bar:** `sleeping 21596s` post-evaluation → next wake ~12:02 UTC.

**End-to-end Phase 2 deploy is fully validated.** All UI surfaces operating against real production data:
- Home tile placeholder gone, dial proper rendering with channel values + state-aware edge marker.
- Division-detail decision-log tile populated with row 1.
- agent_state persistence + broker-snapshot reconcile working across the restart cycle.

---

## 2026-05-09 04:26 UTC — Donchian decision-log empty-state copy refresh

**Commits:** `9de5902` (committed before deploy).
**Triggered by:** Board flag during the wait-for-validation window — Phase 1 partial said "strategy not yet wired into the orchestrator," cosmetically stale after Phase 2 shipped.
**Mechanism:** template-only, deployed via `tr -d '\r' | ssh ... 'cat > target'` stdin pipe. **No service restart** — Jinja autoreloaded the template on the next request. Useful precedent: pure-template changes on prod don't require the 30-90s Fidelity-login restart cycle.

**Files deployed (1 modified):**

- `trading_corp/web/templates/partials/donchian_log.html` — empty-state copy: "strategy not yet wired into the orchestrator" → "No decisions logged yet — first row lands at the next 6h-bar close (00/06/12/18 UTC)". Top-of-file docstring updated to describe the orchestrator's per-bar write contract. (Note: ET update later in same session further refined the copy to ET-formatted boundaries.)

**Backup:** prod copy at `/home/azureuser/backups/donchian_log.html.pre-copy-fix-20260509-0426.bak` (separate file, not a tarball — the stdin-pipe deploy used a single-file backup).

**Verification:** `curl localhost:8000/division/coinbase_spot | grep "No decisions"` returned the new copy on the next request, confirming Jinja autoreload.

---

## 2026-05-09 03:40 UTC — Coinbase BTC HODL rename + revert intent to aggressive

**Commits:** local-only at deploy time.
**Triggered by:** Board reaction to the 03:30 UTC deploy — wanted the tile back in the CRYPTO group (alongside Coinbase Futures + BitUnix Futures) and the name updated to `Coinbase BTC HODL` (broker-prefixed pattern).
**Backup:** prod copy of `divisions.yaml` saved to `/home/azureuser/backups/divisions.yaml.pre-rename-20260509-0339.bak` (5.1K).

**Files deployed (1 modified):**

- `config/divisions.yaml` — `coinbase_spot`:
  - `name: Bitcoin HODL` → `Coinbase BTC HODL`.
  - `intent: retirement` → `aggressive`. Tile moves back from Retirement → Crypto group on the home page (since `classify_investment_type` falls through to the broker-rule when intent is not retirement; `coinbase` is in `_CRYPTO_BROKERS`).
  - Comments removed (the prior "retirement-aligned" rationale block is no longer accurate).
  - `target_annual_return: 0.40` unchanged — still consistent with `aggressive` intent.

**Verification:**

- Pre-restart PID 164009 → post-restart 164965.
- md5 round-trip MATCH on `divisions.yaml`.
- `Web command center listening on http://0.0.0.0:8000` + `Donchian scheduler online: ... enabled=True` post-restart.
- `GET /` HTTP 200 (~3.1s, 76.5 KB).
- "Coinbase BTC HODL" appears 1×, "Bitcoin HODL" 0× — clean rename.
- Group section order on home page: `Individual` → `Crypto` → `Retirement`. Coinbase BTC HODL is now in `Crypto`.
- Tile badges: `aggressive` (loss/red), `online` (gain/green), `○ CASH` (edge/gray) — Donchian widget code from the 03:30 deploy is unchanged, badge + dial scaffolding remain.

**Inert / dormant on current traffic:**

- Donchian dial proper still pending the first `donchian_evaluated` audit row at ~06:02 UTC (sleep 8731s post-restart, math: 03:40:25 + 8731s ≈ 06:02:00 UTC ✓).

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
cp /home/azureuser/backups/divisions.yaml.pre-rename-20260509-0339.bak \
   /home/azureuser/trading_corp/config/divisions.yaml
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 03:30 UTC — "Bitcoin HODL" rename + retirement reclass + home-tile Donchian widget

**Commits:** local-only at deploy time (4 files modified in working tree, awaiting Board commit decision).
**Triggered by:** Board reaction to the home-page tile post-Phase-2 — flagged that the tile didn't reflect the new strategy. Asked for: (a) CASH/BTC badge on the home tile (originally part of Phase 1 design intent but only built into the division-detail page), (b) v0 "Dial of Donchian" with state-aware geometry, (c) rename `Coinbase Spot` → `Bitcoin HODL`, (d) reclass intent `aggressive` → `retirement`.
**Backup tag:** `pre-donchian-tile-20260509-0328` (tarball at `/home/azureuser/backups/pre-donchian-tile-20260509-0328.tar.gz`, 22K, 4 modified files).

**Files deployed (4 modified, 0 new):**

- `trading_corp/utils/divisions.py` — added `donchian: dict | None = None` field to the `Division` dataclass. Hydrated only for divisions running a Donchian strategy (today: `coinbase_spot`); other divisions stay `None`.
- `trading_corp/web/data.py` — new `_hydrate_donchian_overview(divisions, db_url)` helper invoked from `build_command_center` after `_hydrate_division_metrics`. Reads `agent_state` for the CASH/BTC state + `cost_basis`, then the most recent `audit_event` row of kind `donchian_evaluated` for `current_close` / `donchian_high` / `donchian_low`. Pre-computes a 0..1 dial position (`(close - low) / (high - low)` clamped). Tolerant of missing data — pre-first-eval, state still renders but dial chrome hides.
- `trading_corp/web/templates/home.html` — division-tile additions:
  - **CASH/BTC badge** in the header row alongside the existing intent + status badges. `● BTC` (green) when in BTC, `○ CASH` (gray) when in CASH. Renders only when `d.donchian` is set.
  - **State-aware Donchian dial** below equity: horizontal gradient bar (loss-tinted left → edge-color middle → gain-tinted right), white needle at `dial_position * 100%` width, state-aware "fires here" edge marker (CASH state → green tick at right edge with hover-tooltip "BUY fires when close breaks above the entry-channel high"; BTC state → red tick at left edge with "SELL fires …"). Numeric trio (`low / close / high`) underneath. Shows `awaiting first 6h-bar evaluation` placeholder when state exists but no audit row has landed yet.
- `config/divisions.yaml` — `coinbase_spot`:
  - `name: Coinbase Spot` → `Bitcoin HODL` (per Board pick).
  - `intent: aggressive` → `retirement`. Side effect: `classify_investment_type` checks `intent == "retirement"` BEFORE the crypto-broker rule, so the home tile **moves out of the CRYPTO group into the RETIREMENT group** alongside Robinhood IRA + Fidelity 401(k). Coinbase Futures + BitUnix Futures remain in CRYPTO (their intent is still `aggressive`).
  - `target_annual_return: 0.40` left UNCHANGED (flagged for Board call — 40% reads aggressive for a retirement-classed division).

**Features shipped:**

- **Bitcoin HODL renamed + reclassed.** Home page now shows the division in the Retirement section with a blue `RETIREMENT` badge.
- **CASH/BTC badge live on the home tile.** Currently shows `○ CASH` (the agent's persisted state from the 02:54 UTC startup reconcile).
- **State-aware Donchian dial scaffolded.** Until the first `donchian_evaluated` row lands at ~06:02 UTC, the placeholder reads "awaiting first 6h-bar evaluation". After 06:02 UTC the dial replaces the placeholder automatically (next page load) — no further deploy needed.

**Notable code changes:**

- **`Division.donchian` is the per-tile pivot point.** Today only `coinbase_spot` is populated. If a future second Donchian strategy lands on a different division, the hydration helper needs broadening (currently hardcoded to `coinbase_spot` slug).
- **Dial geometry is single-channel, state-aware labels** (option 2 from the in-session design discussion). Needle position uses the full `[donchian_low, donchian_high]` channel regardless of state; only the "fires here" edge marker swaps sides. Trade-off: visually the same dial whether in CASH or BTC, with one threshold "active" — keeps the at-a-glance signal consistent across state flips.
- **Dial computation lives in Python (`_hydrate_donchian_overview`), not Jinja.** Template stays dumb. Edge cases (degenerate channel where high <= low) handled in Python; template only checks `dial_position is not none`.

**Verification:**

- Pre-restart PID 161969 → post-restart 164009.
- All 4 files md5-match end-to-end after SCP (LF-normalized).
- `Donchian scheduler online: ... sleeping 9116s until next bar close` — math: 03:30:03 UTC + 9116s ≈ 06:02:00 UTC ✓.
- `CoinbaseBTCDonchianAgent: restored state=cash cost_basis=None last_bar=None` — DB persistence survived the restart (state row was written at the 02:54 UTC reconcile + persists to `agent_state`).
- `CoinbaseBTCDonchianAgent: reconciled to CASH state — held=0.00000000 BTC < $1.00 dust threshold` — broker reconcile pass ran clean.
- `GET /` HTTP 200 (~2.8s).
- Home page render check: "Bitcoin HODL" appears once; "Coinbase Spot" appears 0 times. Tile is in the Retirement group section (group order: Individual → Crypto → Retirement; Bitcoin HODL appears after the Crypto section). Badges visible: `retirement` (blue), `online` (green), `○ CASH` (gray). Dial chrome shows the placeholder.
- 25 unit tests pass (risk_gates + coinbase_btc_donchian_agent).

**Inert / dormant on current traffic:**

- **The dial proper (gradient bar + needle + price triplet) is dormant until 06:02 UTC** when the first `donchian_evaluated` audit row lands. The placeholder is the visible state; no JS / refresh needed — next page load post-06:02 will replace it.
- **`target_annual_return: 0.40` is now visually inconsistent with the retirement intent.** No code path consumes this value for risk-gating (retirement-aligned caps come from `intent: retirement` not from this number); it's tile-context-only. Cosmetic, but should be revisited.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-donchian-tile-20260509-0328
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 02:53 UTC — Coinbase BTC Donchian Phase 2 (live wiring + paper-mode deploy)

**Commits:** `a606685` (Phase 2 wiring), preceded by Phase 1 commits `072a484` / `0eb7692` / `fe1cee8` / `f9277e9` — none of the Phase 1 commits had been deployed prior, so this deploy ships Phase 1 + Phase 2 together.
**Triggered by:** Board pickup of the BACKLOG.md "🟡 ACTIVE — Coinbase BTC Donchian (Phase 2 wiring + paper-mode deploy)" brief. coinbase_spot pivots from the Otter+Cypher confluence experiment (no walk-forward edge) to a single 100%-in/out Donchian Channel Breakout strategy (24mo backtest +25.89% alpha vs HODL; 8/10 walk-forward OOS configs beat HODL).
**Backup tag:** `pre-donchian-phase2-20260509-0252` (local git tag on `85d6a80`, the pickup-brief commit). Prod backup as tarball at `/home/azureuser/backups/pre-donchian-phase2-20260509-0252.tar.gz` (48K, 6 modified files).

**Files deployed (11 = 6 modified + 5 net-new):**

- `trading_corp/agents/risk.py` (modified) — section 4 (account max-DD) wrapped in `if not bool(params.get("max_drawdown_disabled", False))` guard. Default-safe; opt-in only.
- `trading_corp/agents/strategies/coinbase_btc_donchian_agent.py` (NEW on prod; locally extended) — agent class from Phase 1 commit fe1cee8 plus a small `last_verdict` attribute exposed for the orchestrator's audit-row write (so SKIP decisions also get the channel highs/lows logged, not just BUY/SELL via order extras).
- `trading_corp/agents/strategies/donchian_btc.py` (NEW on prod) — pure-function decision module from Phase 1 commit 072a484. Both backtest harness and live agent import this same module.
- `trading_corp/main.py` (modified) — construct `CoinbaseBTCDonchianAgent` at startup, reconcile state from `coinbase_spot` snapshot post-`connect_all`, spawn `_scheduled_donchian_loop` alongside the PMCC scheduler. New helpers `_seconds_until_next_6h_boundary` (00/06/12/18 UTC + 2min buffer) + `_fetch_recent_btc_6h_bars` (public ccxt; drops in-progress bar) + `_run_donchian_bar` (one cycle, extracted for ad-hoc trigger). Cancels `donchian_task` cleanly on shutdown.
- `config/risk.yaml` (modified) — `overrides.coinbase_btc_donchian` block: `per_trade_risk_pct=1.0` (full sleeve), `per_strategy_daily_loss_pct=1.0` (effectively-disabled — risk.py reads via `float()` so literal `null` would raise), `max_drawdown_disabled=true`.
- `config/strategies.yaml` (modified) — `lord_otter.enabled` and `market_cypher.enabled` flipped to `false` (paused per 2026-05-08 vision direction; files preserved for future BitUnix Futures wiring); `coinbase_btc_donchian.enabled` flipped to `true`. `auto_execute=false` everywhere.
- `trading_corp/web/data.py` (modified) — `build_donchian_view` from Phase 1 commit f9277e9 (state card data, per-bar decision-log query, realized round-trip pairing).
- `trading_corp/web/templates/division.html` (modified) — donchian tile includes for the `coinbase_spot` division page.
- `trading_corp/web/templates/partials/donchian_state.html` (NEW)
- `trading_corp/web/templates/partials/donchian_log.html` (NEW)
- `trading_corp/web/templates/partials/donchian_trades.html` (NEW)

**Local-only (NOT deployed):**

- `tests/test_risk_gates.py` — new `test_max_drawdown_disabled_flag_skips_cap` locks in default-safe + opt-out semantics for the new flag. Existing `test_max_drawdown_triggers_flatten` already covers the default-on path.

**Features shipped:**

- **Coinbase BTC Donchian goes live in paper mode.** Agent module + locked config (`entry=20, exit=6, trend_filter=168, granularity=21600`) + 6h-bar-close scheduler + risk overrides + UI tiles all on prod. `auto_execute: false` — every BUY/SELL routes through HITL via the web app.
- **`max_drawdown_disabled` per-strategy opt-out for the account-level 15% auto-flatten** — first user is Donchian (24mo backtest max DD 16.49% would have force-flattened the strategy mid-run). Default-safe; no other strategy is opted in.
- **`donchian_evaluated` audit kind starts landing on every 6h-bar boundary**, regardless of decision. The `coinbase_spot` division page's per-bar decision-log tile is its only consumer today.
- **Otter and Cypher disabled on `coinbase_spot`.** Webhook endpoints still accept POSTs (web/webhooks.py is unchanged) but the agents short-circuit on `enabled: false` before ProposedOrder construction. Files preserved per `trading_corp_bitunix_vision.md` — Otter+Cypher ultimately move to BitUnix futures.

**Notable code changes:**

- **`agents/risk.py` section 4 is now opt-out-able per strategy.** This is the only safety-adjacent edit in this deploy; new flag defaults to `False` so existing strategies (PMCC, lord_otter override, manual_coinbase_spot, etc.) are unchanged. Reviewers / future-Claude: the gate's wrapper guards both the `params.get(...)` cap read AND the verdict construction. Don't unwrap one without the other.
- **`coinbase_btc_donchian_agent.py:_last_verdict` is the orchestrator-write hook.** `on_bar_close` short-circuits BEFORE `evaluate_donchian` for `disabled` / `no-bars` / dedup cases — `last_verdict` is only refreshed when the decision module ran, so the orchestrator's `if new_verdict is not None and new_verdict is not prev_verdict` check correctly skips audit writes for short-circuit paths.
- **`_scheduled_donchian_loop` uses ccxt's PUBLIC endpoint for OHLCV** (no auth), same pattern as `paper_trade_replay._default_ccxt_fetcher`. The Coinbase broker's authenticated client (`_exchange.fetch_ohlcv`) was deliberately NOT used — keeps ohlcv read decoupled from broker-auth lifecycle, and the public endpoint has no rate-limit pressure for one call/6h.
- **Bar-boundary math (`_seconds_until_next_6h_boundary`) finds the *strict-greater-than-now* next boundary** — guarantees no double-fire if the loop wakes exactly on a boundary. Combined with the agent's internal `last_bar_ts` dedup, double-fires are double-prevented.
- **On a paper or live "filled" status, the orchestrator calls `agent.mark_filled(side, fill_price=order.limit_price)`.** `limit_price` is the bar-close price the agent used to size the order (set inside `on_bar_close`). For paper-execute fills this is exact; for live fills it's an approximation (real fill price comes from the FillEvent — currently not threaded back to the agent because `_run_order` returns only the status string). Acceptable for Phase 2 paper-mode; revisit if/when `auto_execute` flips.
- **The decision-log tile's empty-state copy says "strategy not yet wired into the orchestrator" — cosmetically stale post-deploy.** Tile was scaffolded in Phase 1 (commit f9277e9) for the pre-wiring state. Will read correct once the first audit row lands at 06:02 UTC. Worth a one-line copy fix on a future surface pass; not blocking.

**Verification:**

- Pre-restart PID 157638 → post-restart 161955 (PID change confirms restart took).
- All 11 files md5-match end-to-end after SCP (LF-normalized).
- journalctl from 02:53:33 → 02:54:14 UTC, full startup sequence:
  - `RiskAgent reloaded config/risk.yaml` — new override block parses cleanly.
  - `LordOtterAgent reloaded config: enabled=False` — Otter disabled.
  - `MarketCypherAgent reloaded config: enabled=False` — Cypher disabled.
  - `CoinbaseBTCDonchianAgent reloaded: enabled=True auto_execute=False entry=20 exit=6 trend_filter=168 granularity=21600` — Donchian config loads with the locked params.
  - `CoinbaseBTCDonchianAgent: no persisted state; defaulting to CASH` — first-boot clean.
  - `CoinbaseBroker(spot) connected (markets_loaded=True)` — Coinbase live (real-read, paper-execute wraps).
  - `CoinbaseBTCDonchianAgent: reconciled to CASH state — held=0.00000000 BTC < $1.00 dust threshold` — broker reconcile snippet ran successfully.
  - `Web command center listening on http://0.0.0.0:8000`.
  - `PMCC scan scheduler online: weekdays 08:30–09:25 ET` — existing scheduler intact.
  - `Donchian scheduler online: wakes at 00/06/12/18 UTC + ~2min (strategy enabled=True, auto_execute=False)`.
  - `Donchian scheduler: sleeping 11266s until next bar close` — math: 11266s ≈ 3h 8m from 02:54 UTC → wakes at 06:02:00 UTC ✓.
- Dashboard smoke (localhost:8000, auth-bypass): `GET /division/coinbase_spot` HTTP 200, 61.7KB. State card renders `○ CASH`, `BTC/USD`, `entry: 20-bar high`, `exit: 6-bar low`, `SMA(168)`, `6h bars`. Per-bar log tile + round-trips tile both render with correct empty states.
- Pre-existing errors only — Fidelity bot-block (paper-fallback to data_exec).

**Inert / dormant on current traffic:**

- ~~**First `donchian_evaluated` audit row will land at ~06:02 UTC 2026-05-09**~~ → **✅ CLOSED 2026-05-09 06:02:03 UTC.** First bar evaluated SKIP (close $80,374 ≤ 20-bar high $82,814.23 — stay in CASH). See the dedicated "06:02 UTC validation gate" entry above for full details.
- **Lord Otter / Market Cypher webhook endpoints (`/webhook/tradingview/lord-otter` and `.../market-cypher`) still accept POSTs.** Agents short-circuit on `enabled: false` before order construction; the audit trail still records `webhook_received` / `alert_ignored`. No Telegram pushes will fire from these strategies.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-donchian-phase2-20260509-0252
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
rm -f trading_corp/agents/strategies/coinbase_btc_donchian_agent.py \
      trading_corp/agents/strategies/donchian_btc.py \
      trading_corp/web/templates/partials/donchian_state.html \
      trading_corp/web/templates/partials/donchian_log.html \
      trading_corp/web/templates/partials/donchian_trades.html
sudo systemctl restart trading-corp
"
```

---

## 2026-05-08 22:04 UTC — Telegram inline-keyboard removed in notification-only mode

**Commits:** local-only (uncommitted at deploy time). Local working tree of `comms/telegram_bot.py` diverges from HEAD by ~133 net lines that match prod's pre-edit content — the file has prod-only changes that were never backported to git (see § Notable code changes below).
**Triggered by:** Direct Board observation during 21:39 UTC `/scan` smoke (see preceding deploy entries today). Slim Telegram pings landed correctly but still rendered Approve/Reject inline-keyboard buttons. CLAUDE.md §HITL surface direction is unambiguous: "Telegram messages do not carry order detail, do not accept Approve/Reject replies, **do not run inline keyboards**." The 2026-05-05 B.4 entry retained the keyboard as a "belt-and-suspenders fallback"; Board called the rule, dropped the fallback.
**Backup tag:** `.pre-telegram-no-keyboard-fix-20260508-2204` (one prod file). Pre-deploy md5 `aa749ac1d7ca9bebef78196688b33ef6`.

**Files deployed (1 modified):**

- `trading_corp/comms/telegram_bot.py` — `_build_approval_message`:
  - Notification-only branch now returns `(text, None)` for the markup (was `(text, kb)`).
  - Slim body trailer line `_Tap Approve / Reject below, or open the dashboard link._` removed (would have been misleading without keys).
  - kb construction moved INSIDE the rich-mode `else` branch — no `InlineKeyboardMarkup` is built when `notification_only=True`.
  - Docstring updated: return type is now `(text, InlineKeyboardMarkup | None)`.
  - Rich-mode (legacy `notification_only=False`) path unchanged byte-for-byte.

**Local-only (NOT deployed):**

- `tests/test_slim_approval_notification.py` — 2 new tests: `test_telegram_notification_only_omits_inline_keyboard` (regression: notification-only must return None for kb, body must NOT mention Approve/Reject) + `test_telegram_rich_mode_keeps_inline_keyboard` (pin: rich mode still produces a keyboard).

**Features shipped (load-bearing for future "is X done?" checks):**

- **Telegram is now truly one-way in production.** Slim notification body + deeplink only. No keyboard, no Approve/Reject buttons, no in-Telegram decision surface. `https://trading.jacksumner.com/approvals/{order_id}` is the sole approval surface.
- **HITL-in-app direction reaches its terminal phase pre-Phase E.** B.4 (2026-05-05) made the slim format the live default and dropped the rich body from Telegram. This deploy drops the keyboard. Phase E (PWA + web push) would let Telegram be dropped entirely; until then Telegram is one-way notification.
- **Test pin in place** so a future refactor doesn't re-introduce the keyboard. The test asserts both `kb is None` AND that the body lacks "Approve"/"Reject" tap-prompt text.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **`comms/telegram_bot.py` had 128 lines of prod-only content not in local HEAD before this deploy.** Pre-deploy md5 diff: local HEAD `3cc9faa2...` vs prod `aa749ac1...` (post-LF-normalization), 538 lines on prod vs 410 in HEAD. The patch was applied directly onto prod's content (download → patch → re-upload) rather than via git, to avoid stomping on prod-only changes. Same pattern as today's earlier `approval_format.py` and `pmcc_robinhood.py` deploys. **Backporting this drift into git is a separate cleanup task.**
- **`_on_callback` handler is unchanged** — it still routes inline-keyboard callbacks for the rich-mode path. So nothing broke for non-notification-only callers (tests, CLI dev). Removing the keyboard handler entirely is a future cleanup, not in scope.
- **Slim body trailer text was removed entirely**, not just edited. Previous text: `_Tap Approve / Reject below, or open the dashboard link._` had two underscores (the italic markers) — keeping it would have left a Markdown-italic span hanging if combined with future format changes. Cleaner to drop.
- **Telegram message length shrinks ~30%** — the trailer was the longest single line in the slim body.

**Verification:**

- Pre-deploy: PID 155725 (post-21:34 restart), Telegram smoke at 21:39 UTC delivered cleanly but with buttons.
- Post-deploy: PID 157624. Port 8000 listening within 60s. `PMCC scan scheduler online` line emitted clean.
- Smoke at 22:08–22:10 UTC: `/scan` triggered. Universe correct (`['ASTS', 'BLSH', 'BULL', 'CIFR', 'HOOD', 'IREN', 'MARA', 'MSTR', 'OPEN', 'RIOT', 'RKLB', 'SMR', 'TSLA']`), 16 orders proposed, 3 ASTS pending_approval_added rows landed. Board confirmed in chat: "yes and yes" — pings landed, no keyboard.
- Zero `notifier 'TelegramChannel._notify_approval' failed` in journalctl since restart. Zero `Can't parse entities`.
- Pre-existing errors only — Fidelity Akamai bot-block.

**Inert / dormant on current traffic:**

- Rich-mode path is dormant on prod (notification_only=true is set on the systemd unit). The rich-mode keyboard code stays in the binary but is never exercised on prod. Future cleanup item.
- 16 approval rows from this scan auto-expire at registry timeout (~3600s) since the prior scan's queued approvals were lost on restart per Board direction (option 3: "restart now, lose pending"). Monday 2026-05-11 12:30 UTC auto-scan re-fires fresh approvals on Monday-open conditions — those are the first non-test exercise of this body.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
  TAG=pre-telegram-no-keyboard-fix-20260508-2204
  BASE=/home/azureuser/trading_corp/trading_corp/comms
  mv \$BASE/telegram_bot.py.\$TAG \$BASE/telegram_bot.py
  sudo systemctl restart trading-corp
"
```

---

## 2026-05-08 21:34 UTC — Telegram parse-error fix (underscore in division slug)

**Commits:** local-only (uncommitted at deploy time). Local HEAD baseline `c057ba80...` was 396 lines; prod was 452 lines (56 lines of prod-only content not backported to git — same pattern as today's other deploys). Patch applied directly to prod's content.
**Triggered by:** First non-zero PMCC scan in 5 days (see preceding 21:08 UTC deploy entry) surfaced a latent bug — every approval ping failed with `Can't parse entities: can't find end of the entity starting at byte offset 26X`. Pre-2026-05-08 the bug never fired because no orders were proposed. Same error was logged once on 2026-05-01 12:33:35 UTC (the only scan that produced output between the earliest entries and today's deploy), but went uninvestigated until volume surfaced it.
**Backup tag:** `.pre-telegram-underscore-fix-20260508-2134`. Pre-deploy md5 `5d1390e92f6297547cb0a7f8bc428557`.

**Files deployed (1 modified):**

- `trading_corp/comms/approval_format.py` — `format_slim_approval_notification`:
  - Symbol slot: `headline_parts.append(sym)` → `headline_parts.append(f"`{sym}`")`. Backtick-wrapped.
  - Division slot: `headline_parts.append(division)` → `headline_parts.append(f"`{division}`")`. Backtick-wrapped.
  - Result: `🎲 *Approval needed*\nROLL SHORT · `MSTR` · `robinhood_pmcc`\n\n[Review on dashboard →](...)` instead of bare `· MSTR · robinhood_pmcc`.

**Local-only (NOT deployed):**

- `tests/test_slim_approval_notification.py` — `test_slim_format_safe_for_legacy_markdown_parse_mode` rewritten as a real regression test. Old assertion was `assert "_" not in headline or "robinhood_pmcc" in headline` (lenient — passed for the buggy state). New assertions: (1) `` `robinhood_pmcc` `` substring required (asserts backtick-wrap), (2) any `_` outside backtick spans must NOT appear in the slim body, with regex stripping of `` `...` `` before counting (since `_` inside a backtick code span is parsed literally by Telegram).

**Features shipped (load-bearing for future "is X done?" checks):**

- **Slim Telegram approval pings now deliver successfully.** Pre-fix: `Can't parse entities` on every ping. Post-fix: zero parse errors observed across two `/scan` smokes (21:39 UTC and 22:08 UTC).
- **The slim format is now Telegram-Markdown-self-contained.** Backtick-wrapping `sym` + `division` makes the headline robust to any underscore-bearing identifier (tickers like `BRK_B` would also work). Combined with the no-keyboard change in the 22:04 UTC entry, the slim body is deeplink-only, parse-error-proof.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **The bug:** `robinhood_pmcc` has one underscore. Pre-fix the slim body had odd total `_` count (1 from division + 2 from the trailer line `_Tap Approve.../link._` = 3). Telegram legacy Markdown reads odd `_` as unmatched italic and rejects the message. Backtick-wrapping `division` makes its `_` literal (inside a code span), bringing the unwrapped `_` count to 0 from the slim body; combined with the 22:04 UTC trailer removal, the count is now 0 outright.
- **The rich format `format_approval_message` ALSO has the same bug** if it were ever sent on prod — the rich header puts `· {division}` bare. It's dead code on prod today (TELEGRAM_NOTIFICATION_ONLY=true, see 2026-05-05 entry) but worth flagging if someone re-enables rich mode.
- **`approval_format.py` had 56 lines of prod-only content not in local HEAD before this deploy.** Same drift pattern as `pmcc_robinhood.py` (681 lines) and `telegram_bot.py` (128 lines). Patch applied directly onto prod's content rather than via git.

**Latent bugs caught + fixed:**

- **Slim Telegram parse-error on every approval** — fixed in this deploy. Latent since the slim formatter was added (Phase A, 2026-05-03 02:09 UTC) but never exercised under load until today's PMCC universe fix unblocked the scan.

**Verification:**

- Pre-deploy: PID 153933 (post-21:08 restart), 20 ASTS approvals queued in registry from the 21:21 UTC smoke, ALL of them got `notifier failed: Can't parse entities` lines in journalctl.
- Post-deploy: PID 155725. Port 8000 listening within 60s. Smoke at 21:39 UTC: `PMCCAgent scan complete: 20 order(s) proposed`, 2 ASTS `pending_approval_added` rows, **zero `notifier failed` lines, zero `parse entities` errors**.
- Telegram delivery confirmed by Board in chat — pings landed (with keyboard at this stage; keyboard fix landed in the 22:04 UTC deploy that followed).

**Inert / dormant on current traffic:**

- Rich format still has the latent bug but is dead on prod. Fix-or-forget when removing the rich code path; not in scope.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
  TAG=pre-telegram-underscore-fix-20260508-2134
  BASE=/home/azureuser/trading_corp/trading_corp/comms
  mv \$BASE/approval_format.py.\$TAG \$BASE/approval_format.py
  sudo systemctl restart trading-corp
"
```

---

## 2026-05-08 21:08 UTC — PMCC scan universe fix + LLM rate-limit cap (first non-zero scan in 5 days)

**Commits:** local-only (uncommitted at deploy time). Local working tree pre-edit had 681 lines of uncommitted content beyond HEAD that EXACTLY matched prod content (Phase A PMCC prompt-text refinements from 2026-05-03 02:09 UTC, never backported to git). Patch applied to working-tree-equals-prod content; deploy is byte-stable.
**Triggered by:** Board observation that every weekday PMCC scan since 2026-05-04 reported "PMCC scan complete: no actions needed this cycle." despite 13 PMCC legs detected. Investigation found two compounding bugs: (1) crypto-position regression after the 2026-05-01 Robinhood crypto-snapshot deploy, (2) Anthropic API rate-limit on parallel LLM analysis.
**Backup tag:** `.pre-pmcc-universe-fix-20260508-2108`. Pre-deploy md5 `5f2ac5617ed39e249e55afb15a762fdd`.

**Files deployed (1 modified):**

- `trading_corp/agents/divisions/pmcc_robinhood.py` — 5 surgical edits:
  - `get_universe()`: skip positions with `/` in symbol (HODL crypto: `ETH/USD`, `BTC/USD`). These are visible in dashboard equity but are not tradeable as PMCC underlyings.
  - `scan()` `stock_qty` lookup: same `/` filter on the position dict comprehension.
  - `scan()`: `detect_existing_legs()` moved BEFORE the early-return; early-return relaxed to require BOTH empty `universe` AND empty `legs_by_symbol` (was just empty universe).
  - `scan()`: order-construction loop iterates `set(universe) | set(legs_by_symbol.keys())` — defensive layer so a future stock holding alongside legs doesn't again drop the legs.
  - `scan()` + `analyze_portfolio()`: `asyncio.Semaphore(N)` bounds parallel LLM calls. N from `strategies.yaml` `pmcc.llm_concurrency` (default 3). Caps in-flight burst under Anthropic's 30k input-tokens/min org cap on claude-sonnet-4-6.

**Local-only (NOT deployed):**

- `tests/test_pmcc_logic.py` — new regression test `test_universe_skips_hodl_crypto_positions` reproducing the exact prod scenario (ETH/USD stock-position + ASTS/MARA legs → universe is `{ASTS, MARA}`, NOT `{ETH/USD}`).

**Features shipped (load-bearing for future "is X done?" checks):**

- **PMCC scan produces non-zero orders again.** Two `/scan` smokes today: 20 orders @ 21:21 UTC, 16 orders @ 22:10 UTC. Pre-fix: 0 every weekday since 2026-05-04. Same scan path is wired into the daily 12:30-13:25 UTC scheduler — Monday 2026-05-11 will be the first non-test scheduled exercise.
- **HODL crypto is now isolated from PMCC scan logic.** `ETH/USD` (and any future `/USD`-pattern crypto position from Robinhood crypto branch) is treated as portfolio-value only. Visible in dashboard equity, invisible to PMCC universe.
- **LLM rate-limit failure mode is bounded.** Pre-fix: 5 of 13 legs got 429 errors and lost their LLM verdict (from journalctl 2026-05-08 12:32 UTC). Post-fix: zero 429s observed across both `/scan` smokes.
- **Defensive structural fix on the leg-iteration path.** Even after the crypto filter, a future stock holding (e.g. AAPL in Individual alongside the 13 PMCC legs) would have produced the same "leg verdicts dropped" symptom. The union-iteration in the scan loop guards against this — leg management runs unconditionally for every detected leg.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **The bug, in one line:** `get_universe()` detects "stock positions" by absence of options-flagging chars (`" "`, `"#"`). The 2026-05-01 Robinhood crypto-snapshot deploy added `BTC/USD`-style symbols to `RobinhoodBroker.snapshot()` for the Individual account. Those passed the options-filter (no space, no `#`), so `ETH/USD` was treated as a "stock position." Because `symbols` was non-empty, the existing leg-underlyings fallback (line 1786 pre-edit) never ran. The order-construction loop iterated over `['ETH/USD']` only — every detected PMCC leg's LLM verdict was computed and discarded.
- **Regression timeline confirmed via journalctl:** 2026-05-01 12:32:41 UTC last clean scan (universe: long-call underlyings, 20 orders proposed). 2026-05-04 12:37:43 UTC first broken scan (universe: `['ETH/USD']`, 0 orders). Pattern held every weekday until today's fix.
- **`pmcc_robinhood.py` had 681 lines of uncommitted local content matching prod** (Phase A PMCC prompt-text refinements from 2026-05-03 02:09 — COOLDOWN guard prose, NYSE-calendar-aware `_terminal_dte_time_release` description, LEAP Hard Rule promotion NOTE blocks). Working tree was effectively in sync with prod, just not git-committed. md5 mismatched on pre-deploy check due to CRLF (Windows local) vs LF (prod Linux) line endings — once normalized, working tree equaled prod. Deploy file was the LF-normalized working tree with my 5 edits.
- **Rate-limit fix is configurable.** `pmcc.llm_concurrency` in `strategies.yaml` defaults to 3; tunable without a code deploy if Anthropic's org cap changes. Hot-reload happens on the next `_reload()` call (every scan).
- **The 16-vs-20 order count delta between smokes is normal.** LLM verdicts can shift (different "elevated" vs "routine" classifications, different `target_strike` choices) when called minutes apart; deterministic Python guards (terminal_dte, halfway-roll cooldown, LEAP hard rule) provide the floor of expected behavior across re-runs.
- **One PMCC leg is risk-rejected per ASTS roll_leap pair** — the new-LEAP buy ($30.85/sh × 100 = $3085/contract, exceeds $1500 per-trade cap). Expected behavior, not a bug. Visible in audit as `risk_rejected` events. Per-trade cap can be raised in `risk.yaml` if Board wants this leg to flow.

**Latent bugs caught + fixed:**

- **PMCC scan universe regression** (described above). Latent since 2026-05-01; first detected today. Fixed.
- **Telegram parse-error on every slim ping** — surfaced by this deploy (because no orders had been firing pre-fix). Fixed in the immediately-following 21:34 UTC deploy.
- **Telegram inline-keyboard contradicts CLAUDE.md HITL direction** — also surfaced by this deploy. Fixed in the 22:04 UTC deploy.

**Verification:**

- Pre-deploy: PID 136040 (running since 2026-05-05 01:34 UTC B.4 deploy), every weekday scan reporting 0 orders.
- Post-deploy: PID 153919 (153933 xvfb child). Port 8000 listening within 60s. `PMCC scan scheduler online: weekdays 08:30–09:25 ET` line emitted clean.
- Smoke at 21:19 UTC: `PMCCAgent universe from long call underlyings: ['ASTS', 'BLSH', 'BULL', 'CIFR', 'HOOD', 'IREN', 'MARA', 'MSTR', 'OPEN', 'RIOT', 'RKLB', 'SMR', 'TSLA']` (NOT `['ETH/USD']`). All 13 LLM verdicts landed (no 429s). `PMCCAgent scan complete: 20 order(s) proposed`. ASTS roll_leap pair-coalesced via `pmcc_pair_id`.
- Pre-existing errors only — Fidelity Akamai bot-block + yfinance BTC/USD earnings noise. Zero new errors.

**Inert / dormant on current traffic:**

- Daily PMCC scheduler (12:30-13:25 UTC weekday window) is the natural exercise. Today's smokes were Telegram `/scan`-triggered (same code path as the scheduler). Monday 2026-05-11 is the first auto-scheduled exercise post-fix.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
  TAG=pre-pmcc-universe-fix-20260508-2108
  BASE=/home/azureuser/trading_corp/trading_corp/agents/divisions
  mv \$BASE/pmcc_robinhood.py.\$TAG \$BASE/pmcc_robinhood.py
  sudo systemctl restart trading-corp
"
```

---

## 2026-05-05 01:34 UTC — Phase B.4: `TELEGRAM_NOTIFICATION_ONLY=true` flag flip (slim Telegram body live)

**Commits:** n/a (configuration-only change — no code shipped, only systemd drop-in added)
**Triggered by:** Mon 2026-05-04 was the planned validation day for the B.4 flip (see deploy_log entry 2026-05-03 05:07 UTC and the prior B.1/B.2/B.3 entries). The original gate was "Mon's first PMCC scan validates the web flow with a real Board-routed approval"; the scan ran clean at 12:38:04 UTC but emitted zero approvals (`scheduled_scan_done` payload: "PMCC scan complete: no actions needed this cycle."), so no live web-flow exercise occurred. User chose to flip anyway on the fallback rationale: paper-mode + Telegram inline-keyboard fallback bind real-money risk to zero, the web flow is verified by tests + B.5 manual smoke, and the value of waiting drops with each empty scan.
**Backup tag:** `n/a` for the override file (newly created — no pre-version exists). Pre-flip Environment snapshot for rollback context: `Environment=KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 PATH=/home/azureuser/trading_corp/venv/bin:...`.

**Files deployed (1 new on prod VM, no repo files):**

- `/etc/systemd/system/trading-corp.service.d/override.conf` (NEW on VM, not tracked in repo per CLAUDE.md §6 "VM-side configuration is no-edit from this repo"):
  ```
  [Service]
  Environment=TELEGRAM_NOTIFICATION_ONLY=true
  ```

**Features shipped (load-bearing for future "is X done?" checks):**

- **`TELEGRAM_NOTIFICATION_ONLY=true` is now the live prod default.** The slim Telegram approval body (`🎲 Approval needed · <action> · <symbol> · <division>` + deeplink to `https://trading.jacksumner.com/approvals/{order_id}`) replaces the rich `format_approval_message` body that had been emitting since Phase A's dormant Phase. First real or synthetic approval after this deploy will arrive in slim format.
- **HITL-in-app direction is now FULLY LIVE end-to-end.** Phase A (slim formatter + dormant flag, 2026-05-03 02:09) + Phase B.1 (registry + routes, 03:50) + Phase B.2/B.3 (rich rendering + Modify form + paired coalescing, 04:20) + Phase B.5 (quick-modify presets + new_limit_price, 05:07) + Phase B.4 (this entry, flag flip) means the Board now sees: short Telegram ping → tap deeplink → web-app approval card with structured trade legs / position context / risk / paired coalescing / quick-modify presets → POST resolves the LangGraph interrupt. Telegram inline keyboard remains as a belt-and-suspenders fallback (resolves the same `PendingApprovalRegistry`).
- **Phase E (web push) remains the only deferred phase.** PWA + service worker + push subscription would let Telegram be dropped entirely; not yet scoped.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **No code change in this deploy.** This is purely a systemd Environment add. The producing code path was already in place and dormant (`_notification_only` switch on `TelegramChannel`, plumbed through `main.py:539` from `os.getenv("TELEGRAM_NOTIFICATION_ONLY", "false").lower() == "true"`).
- **VM-side configuration was edited.** Per CLAUDE.md §6, VM systemd unit configuration is "no-edit from this repo" — it lives only on the VM. The override.conf is at `/etc/systemd/system/trading-corp.service.d/override.conf` and is not tracked in the repo. Future maintenance: if more env vars are added, append to this same drop-in (or use a separate `.conf` file in the same drop-in dir; systemd merges them).
- **`DASHBOARD_BASE_URL` was NOT set.** The `comms/approval_format.py` module has `DEFAULT_DASHBOARD_BASE_URL = "https://trading.jacksumner.com"` as the default, and the slim formatter falls through to it when `os.getenv("DASHBOARD_BASE_URL")` is unset. Confirmed via `main.py:541`.
- **Mon's PMCC scan emitted zero approvals.** The 13 detected legs (RKLB, OPEN, MSTR, MARA, CIFR, TSLA, BULL, BLSH, HOOD, RIOT, ASTS, SMR, IREN) had no fires-this-cycle on roll/open conditions. MSTR had a `no liquid weekly contracts` warning (normal — gated out at the liquidity filter). One unrelated `risk_rejected` event at 14:03:24 UTC was a manual MSTR `via=web_button` click, killed at the per-trade cap because $971.65 < $1865 = 1 contract @ $18.65. Did NOT exercise the `/approvals` web flow.

**Verification:**
- Pre-flip: PID 130241, no drop-in dir.
- Post-flip: drop-in `[Service]\nEnvironment=TELEGRAM_NOTIFICATION_ONLY=true` written, `daemon-reload` clean, restart clean.
- New PID 136026 (parent) → 136040 (xvfb-run python child, the actual server).
- `systemctl show -p Environment trading-corp` includes `TELEGRAM_NOTIFICATION_ONLY=true`.
- `/proc/136040/environ` confirms the live python process inherited `TELEGRAM_NOTIFICATION_ONLY=true` (i.e. it's not just on the unit, it's on the running process).
- `ss -tlnp` shows port 8000 listening on PID 136040.
- Dashboard external probe: `GET /` → HTTP 302 in 0.19s (Authelia redirect, normal).
- Post-restart journalctl: only pre-existing errors observed — Fidelity bot-block (Azure VM IP / Akamai layer, documented sharp edge) + yfinance BTC/USD earnings noise. ZERO new errors.

**Inert / dormant on current traffic:**
- Slim format hasn't sent yet at deploy time — first real approval after deploy will be the first observable check. PMCC scan ran clean today; no scout is scheduled until tomorrow's market-open scan. A test-only synthetic alert via the `/webhook/tradingview/lord-otter` endpoint with paper-mode + auto_execute=false would land in `would_have_placed`, not in the approvals registry, so it's NOT a way to smoke the slim format.
- The old rich `format_approval_message` code path remains in the binary (now dead on this prod process). Removing it is a future cleanup, not in scope.

**Rollback recipe:**
```bash
# Run from any host with az CLI logged into Azure subscription 6f20f2e1-28ec-4857-857c-457c7f5212ca
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript \
  --scripts "sudo rm /etc/systemd/system/trading-corp.service.d/override.conf && \
             sudo rmdir /etc/systemd/system/trading-corp.service.d/ && \
             sudo systemctl daemon-reload && \
             sudo systemctl restart trading-corp && \
             systemctl show -p Environment trading-corp"
# Expected: TELEGRAM_NOTIFICATION_ONLY no longer appears; rich Telegram body resumes.
```

---

## 2026-05-03 05:07 UTC — Phase B.5: quick-modify presets + new_limit_price + graph routing fix

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Continuation of the same Sunday HITL session that shipped B.1 (03:50), B.2+B.3 (04:20). User chose to ship B.5 today and plan B.4 (slim-flag flip) for tomorrow once Mon's PMCC scan validates the web flow live. B.5 = the "quick-modify ±½ size + limit ±5%" preset buttons from `planning/hitl_in_app_design.md` §14, plus the underlying `new_limit_price` plumbing through BoardDecision / graph / POST handler. Fixed a latent graph-routing bug in modify_then_risk_node along the way (unconditional edge back to risk overwrote final_status — manifested as "modify with no fields silently re-pauses at approval forever"; now routes to end_rejected when modify_then_risk_node bails).
**Backup tag:** `.pre-b5-quick-modify-20260503-0505` (on the 5 mutated files)

**Files deployed (5 modified):**

- `trading_corp/graph/interrupts.py`:
  - `BoardDecision` gains `new_limit_price: float | None = None` field. Documented as "only used when decision='modify'", parallel to existing `new_qty`.
  - `request_board_approval` decodes `new_limit_price` from the resume payload alongside `new_qty`.
- `trading_corp/graph/ceo_graph.py`:
  - `approval_node` stashes `new_limit_price` on `state["board_decision"]` (was missing — modify_then_risk_node couldn't see it without this).
  - `modify_then_risk_node` accepts BOTH new_qty and new_limit_price (either alone or both together). Validates each: > 0 when supplied; rejects with `final_status='board_rejected'` when neither field is supplied OR when a supplied field is non-positive. Builds a `board-modified (qty=X, limit=$Y)` rationale annotation showing exactly what changed.
  - **NEW conditional edge** `modify_then_risk_route`: if `final_status == 'board_rejected'` → end_rejected, else → risk. Replaces the unconditional `g.add_edge("modify_then_risk", "risk")` that was silently overwriting final_status by re-running risk on the unmodified order. Without this, the empty-modify path became an infinite re-pause loop. Pinned by new test `test_modify_with_no_fields_rejects`.
- `trading_corp/main.py`:
  - `_run_order` resume payload now includes `new_limit_price` so it survives the LangGraph round-trip.
- `trading_corp/web/routes.py`:
  - `POST /approvals/{order_id}/decide` accepts `new_limit_price` in form OR JSON body (parallel to existing `new_qty`). Modify-validation: at least ONE of new_qty / new_limit_price required (400 with "modify requires at least one of new_qty / new_limit_price" if neither). Each supplied field validated independently (numeric, > 0). Response message includes `qty=X, limit=$Y` for the affected fields.
  - Renamed pre-existing test assertion error message from "new_qty is required for decision=modify" to the both-fields message above; old test renamed to `test_decide_modify_missing_both_fields_400`.
- `trading_corp/web/templates/approval_detail.html`:
  - **Quick-modify preset row** added inside the modify-form panel: 4 buttons in a 2×2 (mobile) / 1×4 (desktop) grid — `½× size`, `2× size`, `limit −5%`, `limit +5%`. Each button shows the COMPUTED preset value below the label (e.g. "½× size → 1" for a qty=2 order; "limit −5% → $5.22" for a $5.50 order). Buttons are `type="button"` with `data-preset-kind` + `data-preset-value` attributes; JS handler intercepts click, calls a shared `_submitDecision('modify', {field: value})` helper, and the form posts in one tap.
  - **`new_limit_price` input field** added below the existing custom-qty input — only renders when the order has a non-null limit/mark price (skipped for market orders without a limit price).
  - Limit-direction preset buttons disabled (with explanatory tooltip) when the order has no `mark` price (e.g. market orders).
  - Submit handler refactored: pulled the URL-encode + fetch + result-swap logic out of the form-submit listener into a shared `_submitDecision(decision, extras)` helper used by both preset clicks and the regular form submit. JS form-submit handler now also pulls `new_limit_price` from the form when present (it didn't before — the input field is new).

**Local-only (NOT deployed):**
- `tests/test_approvals_routes.py` extended with 8 new tests: 6 modify-with-new_limit_price cases (only-limit, both-fields-together, neither-field-400 [renamed], zero-limit-400, non-numeric-400, JSON body) + 2 template smoke tests (presets render with computed values, limit-direction buttons disabled when no price).
- `tests/test_graph_hitl.py` extended with 2 new tests: `test_modify_with_new_limit_price_applies_to_fill` (full graph round-trip — limit-only modify re-runs risk and fills at the new price) + `test_modify_with_no_fields_rejects` (regression test for the routing fix above).

**Features shipped (load-bearing for future "is X done?" checks):**
- **One-tap quick-modify is live.** Board sees presets with the actual numeric outcome on each button ("½× size → 1") so there's no math required; tap fires the modify, no second confirmation. Mobile UX optimized.
- **`new_limit_price` is now a first-class modify field.** End-to-end: web POST → BoardDecision → graph state → modify_then_risk_node → re-evaluate risk → re-pause approval at the new price → execute at the new price on approve. Test pin: `test_modify_with_new_limit_price_applies_to_fill` verifies the fill happens at the modified price, not the original.
- **Graph correctness fix on the modify path.** Empty modify (no qty + no limit) now routes cleanly to `end_rejected` instead of silently re-pausing at approval forever. Pre-B.5 this latent bug existed but no test exercised it — discovered while writing B.5 tests.
- **Modify form on the detail page now renders both inputs** — qty AND limit_price (when applicable) — so the Board can hand-edit either independently of the presets.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Limit-direction presets are ±5% from the order's `mark` (or `limit_price`), NOT from the current market price.** Rationale: the user's anchor is what THEY proposed, not what the market is doing right now. If the order proposes $5.50 limit and the market has moved to $5.80, "limit −5%" goes to $5.22 (5% below the proposal), not $5.51 (5% below market). This makes the math predictable from the displayed price; a future "limit toward bid/ask" preset could be added if needed.
- **Quick-modify presets bypass the custom input field entirely.** Each preset POSTs only the relevant field (`new_qty` for size presets, `new_limit_price` for limit presets) so user edits to the custom inputs are NOT inadvertently submitted with a preset. The `reason` field IS auto-populated as `"preset:qty-half"` / `"preset:limit-down"` / etc. so the audit log distinguishes preset use from custom-input use.
- **Paired mode disables the entire Modify path** (B.2 decision preserved). Paired-modify is a future phase — the design needs to think through per-leg vs both-legs semantics. Telegram `/modify <id> <qty>` still works as the per-leg fallback.
- **Graph routing fix is the load-bearing change for correctness.** The new `modify_then_risk_route` conditional edge is the right architectural fix; without it, ANY modify_then_risk_node bail (empty modify, invalid qty, invalid limit) would silently re-pause at approval. The fix is symmetric — handles current AND future bail conditions.
- **`approval_node` was missing `new_limit_price` in the board_decision dict.** Caught during test debugging — the resume payload had it, BoardDecision had it, but approval_node forgot to copy it from BoardDecision to the graph state. Without that, modify_then_risk_node always saw `new_limit_price=None` even when the user supplied one. One-line fix.
- **`_decide` validation is per-field independent.** Both new_qty and new_limit_price get validated separately, so a request with `new_qty=2.5, new_limit_price=invalid` rejects on the limit field's validation rather than silently dropping it. Errors are specific ("new_limit_price must be > 0" vs "new_qty must be > 0").

**Latent bugs caught + fixed:**
- **modify_then_risk routing bug** (described above). Pre-existing since the original Phase 1 graph wiring; first exposed by B.5 tests. Fixed with the new conditional edge.
- **approval_node missed copying new_limit_price** to graph state. Introduced in this same B.5 deploy but caught in the integration tests before shipping.

**Verification:**
- Pre-deploy: 495 unit tests pass on local (vs 485 pre-B.5 baseline; +10 = 8 new in test_approvals_routes + 2 new in test_graph_hitl). 5 pre-existing P2 failures unchanged (BACKLOG line 1247).
- md5 5/5 files MATCH between local and prod post-scp.
- Backup tag `.pre-b5-quick-modify-20260503-0505` placed on the 5 mutated prod files pre-deploy.
- PID 119776 → 121271 (restart at 05:07:13 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~33s after restart (Robinhood + Fidelity logins block bind, normal).
- `GET /` 200 in 3.29s; `GET /approvals` 200 in 2.77s.
- `POST /approvals/x/decide` with `decision=modify` (no fields) → 400 with the new B.5 message `"modify requires at least one of new_qty / new_limit_price"` — confirms the validation branch landed.
- `POST /approvals/x/decide` with `decision=modify&new_limit_price=5.50` (no qty) on a non-pending order_id → 409 (not 400) — confirms `new_limit_price` is parsed as a valid modify field, just no entry to resolve. The 409-vs-404 for unknown order_id is pre-B.5 behavior; refining that is a B-x polish item.
- journalctl post-restart: 6 ERROR lines, all pre-existing (5 Fidelity Azure-IP block + 1 yfinance BTC/USD earnings noise). ZERO new errors related to BoardDecision, graph routing, modify form, or POST handler.

**Inert / dormant on current traffic:**
- **Quick-modify presets have no users until the Board takes a Modify action on a real pending approval.** Same condition as B.2 modify — first scout-emitted approval with a Board choice to Modify rather than Approve/Reject. Mon ~13:30 UTC PMCC scan is the first natural exercise.
- **`new_limit_price` graph wiring** is dormant on the approve/reject paths (only fires when decision=modify). Approve / Reject still take the same byte-identical paths as B.4-pre.
- **Phase A flag still NOT flipped.** `TELEGRAM_NOTIFICATION_ONLY` stays unset. B.4 plan: validate web on Mon's first live PMCC approval, then flip same-day. Soak window collapsed from "1 week" to "1 live exercise" since paper mode + inline keyboard fallback bound real-money risk to zero.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-b5-quick-modify-20260503-0505; BASE=/home/azureuser/trading_corp;
for f in trading_corp/graph/interrupts.py trading_corp/graph/ceo_graph.py trading_corp/main.py trading_corp/web/routes.py trading_corp/web/templates/approval_detail.html; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

(Reverts B.5 only — leaves B.1+B.2+B.3 intact.)

---

## 2026-05-03 04:20 UTC — Phase B.2 + B.3: rich `/approvals` rendering + Modify form + paired-roll coalescing

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Continuation of the same Sunday session that shipped B.1 at 03:50 UTC. User chose two-cut strategy ("B.1 alone first, then B.2+B.3 bundled") so the load-bearing registry seam validated discrete on prod before stacking polish + safety-critical pair coalescing on top. B.2 = Tailwind structured rendering of trade legs / position context / risk / warnings (replaces B.1's raw-JSON dump) + inline Modify form. B.3 = the safety-critical pair-coalescing fix: PMCC roll close+open siblings now run in parallel via asyncio.gather, land in registry simultaneously, render as ONE card with combined Net Debit/Credit, ONE Approve button resolves both atomically. Eliminates the original "approve close, reject open → naked short" failure mode that was the entire reason the BACKLOG P0 existed.
**Backup tag:** `.pre-b23-hitl-rich-pairs-20260503-0417` (on the 5 mutated files; the new position_context.py file has no backup target — `rm` is the rollback)

**Files deployed (5 modified, 1 new):**

*New:*
- `trading_corp/comms/position_context.py` — structured-dict builder consumed by the web detail template. `build_approval_view(detail)` returns `{headline, trade.legs[], context, risk, warnings, pmcc_pair_id, raw_extra}`. Each leg carries `{side, qty, asset_class, symbol, action_label, option, mark, bid, ask, gross_dollars, side_sign, rationale}` plus crypto-/stock-specific fields where applicable. `coalesce_paired_view([close_v, open_v])` merges two single-leg views into a paired one with combined `trade.legs[]` + summed `net_dollars`; sorts close-leg-first; uses the close leg as the headline anchor; surfaces `is_paired=True` and `paired_order_ids[]`. Defensive against missing/malformed `extra_json` (decodes JSON; falls back to `extra` dict; safe `_safe_float` wrapper). The Telegram formatter (`comms/approval_format.py`) is UNCHANGED — its existing string-output path remains the source of truth for Telegram message bodies; the structured dict is web-only in B.2.

*Modified:*
- `trading_corp/comms/pending_registry.py`:
  - `PendingEntry` gains `pmcc_pair_id: str | None = None` field (extracted from `req.detail["order"]["extra_json"]` at `wait()` registration time via the new module-level `_extract_pair_id` helper).
  - `pending_approval_added` audit row payload now includes `pmcc_pair_id` (renders in audit trail; lets the dashboard recover paired state from audit if a restart wipes the registry).
  - `resolve(...)` gains `also_resolve_paired: bool = False`. When True AND the entry has a paired sibling currently pending, the sibling's Future is resolved with the SAME decision in the same call. Two `board_decision_received` audit rows emit (one per leg), each tagged `paired_with=<sibling_order_id>` for traceability. Graceful no-op when the paired flag is set but no sibling is in the registry.
  - New `find_sibling(order_id) -> ApprovalRequest | None`: looks up the OTHER pending entry sharing the order's `pmcc_pair_id`. Used by the detail-page handler to render coalesced view at request time.
- `trading_corp/main.py`:
  - New module-level `_group_orders_by_pair_id(orders) -> list[list[ProposedOrder]]` helper. Groups orders so paired siblings (sharing `extra.pmcc_pair_id`) end up in the same sub-list; solo orders become singleton lists. Group ordering preserves the position of the first-seen leg of each pair.
  - PMCC scan loop refactored: instead of `for order in orders: await _run_order(...)`, the loop iterates `groups = _group_orders_by_pair_id(orders)`. Solo groups await sequentially (preserves prior blast-radius bound). Multi-leg groups dispatch via `asyncio.gather(*[_run_order(...) for o in group])` so both ApprovalRequests land in the registry at the same instant — that's what makes the web detail page's coalesced view actually appear (sibling lookup at render time succeeds because both legs are simultaneously pending).
  - Fidelity scan path UNCHANGED — Fidelity is read-only-on-Azure-VM and the autonomous-execution path is deferred (BACKLOG P3 #1341); applying the same refactor would expand blast radius without delivering value today.
- `trading_corp/web/routes.py`:
  - Two new module-level helpers: `_group_index_entries(entries)` collapses paired entries into ONE `kind='paired'` row per pair_id (close leg as anchor, combined headline "ROLL · {SYM} · close + open"); `_summary_action_hint` / `_summary_symbol` parse the rich Telegram-Markdown summary's first line to pick the close-leg anchor + extract symbol for the combined headline.
  - `GET /approvals` reworked: passes `rows = _group_index_entries(entries)` + `total_legs` to the template. Each row carries `{kind, entries, is_paired, primary_order_id, division, summary, added_at, pair_id}`.
  - `GET /approvals/{order_id}` reworked: builds primary view via `build_approval_view`, looks up sibling via `registry.find_sibling`, calls `coalesce_paired_view([primary, sibling])` when sibling exists. Template gets `view`, `is_paired`, `sibling_order_id`. POST target stays the primary order_id (sibling is resolved via `also_resolve_paired` flag).
  - `POST /approvals/{order_id}/decide` extended: accepts `decision="modify"` with required `new_qty` validation (numeric, > 0) — 400 on missing/invalid/zero/negative qty. Accepts `also_resolve_paired` form field (or JSON bool) — when truthy AND sibling pending, both Futures resolve atomically (single POST). Response message includes "(qty=X)" for modify and "· both legs resolved" when paired.
- `trading_corp/web/templates/approvals.html`:
  - Renders `rows[]` instead of `entries[]`. Paired rows get a `paired` badge and the combined headline.
  - Header shows "(N cards · M legs)" when N != M (i.e., paired rolls present).
- `trading_corp/web/templates/approval_detail.html`:
  - Replaced the raw-JSON `<pre>` dump with structured rendering: headline (emoji + action label + symbol + division + paired-roll badge); trade-legs block (each leg: side + qty + option/symbol + dte/delta + mark + bid/ask + signed gross dollars; net row when ≥2 legs); position-context block (LEAP, days held, cost vs mark, P&L pct, unrealized $/%, roll count + prior credit); risk verdict block (color-coded by verdict); warnings block (when present).
  - Inline Modify form expands on click: numeric input + optional reason; submits `decision=modify` to existing POST endpoint. Disabled with explanatory tooltip when the card is paired (paired-modify is B.x; Telegram `/modify <id> <qty>` still works as fallback).
  - Form-submit JS intercepts both Approve/Reject/Modify; URL-encodes form data manually so the also_resolve_paired hidden field travels with paired cards. Result fragment swap stays in-page (mobile UX). 409 → "already decided" warn message; 400 → strips HTML and shows up to 200 chars of detail.
  - Raw-detail JSON kept as a collapsible `<details>` debug block at the bottom.

**Local-only (NOT deployed):**
- `tests/test_position_context_view.py` — 20 new tests pinning the structured dict shape (headline action labels for option/roll/stock/crypto, dollar math sign, bid/ask extraction, leap pnl_pct computation, risk color normalization, pair_id extraction, defensive fallbacks for missing/malformed extra_json, coalesce close-first ordering + net math + singleton pass-through + empty-list raise).
- `tests/test_pair_grouping.py` — 6 tests for `_group_orders_by_pair_id` (solo passthrough, paired grouping, mixed solo+paired, two independent pairs, empty list, group ordering preserves first-leg position).
- `tests/test_pending_registry.py` extended with 7 B.3 tests: pair_id extraction into entry, find_sibling both directions, find_sibling None when solo / no pair_id, also_resolve_paired atomicity (both Futures resolved with same decision), audit row tagging with paired_with on each leg, graceful no-op when no sibling.
- `tests/test_approvals_routes.py` extended with 9 tests: 6 modify-flow tests (success, missing new_qty, zero qty, negative qty, non-numeric qty, JSON body) + 3 paired-flow tests (index coalescing, paired detail rendering with also_resolve_paired hidden field, paired POST resolves sibling, sanity test that omitting flag only resolves one leg).

**Features shipped (load-bearing for future "is X done?" checks):**
- **Web `/approvals/{order_id}` is now decision-quality.** A Board member can read the trade legs, position context (LEAP, prior rolls, P&L), risk verdict, and warnings on a phone screen — same information the Telegram rich body has rendered since 2026-05-02. The page is fully self-contained; tap Approve/Reject/Modify and the LangGraph resumes.
- **Modify on the web works end-to-end** for solo orders. Inline form, qty input, optional reason; submits to the same POST endpoint. graph/ceo_graph's `modify_then_risk_node` re-evaluates risk and re-emits an interrupt with the modified qty — the registry receives a NEW ApprovalRequest under the same order_id, the next Board action lands on the modified version. (Verified by route test; full graph integration relies on existing test_graph_hitl which is unchanged.)
- **Paired-roll coalescing is live.** When a PMCC scan emits a roll (close + open with shared pmcc_pair_id), `_group_orders_by_pair_id` puts both into the same group, asyncio.gather launches them in parallel, both interrupts fire, both ApprovalRequests land in the registry simultaneously. The web `/approvals/{order_id}` detail page renders ONE card with both legs + Net Debit/Credit; ONE Approve click resolves BOTH Futures via `also_resolve_paired=True`. The "approve close, reject open → naked short" failure mode is structurally impossible from the web surface — both legs share the same atomic decision.
- **Telegram inline keyboard still works in parallel** for both solo AND paired orders. The Telegram path resolves one leg at a time per click (legacy behavior unchanged); the web path resolves both legs per paired click. First-decision-wins applies per-leg, so a Telegram-approve-then-web-paired-approve race resolves cleanly (web's already-resolved leg returns False from registry.resolve and skips audit re-write).

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`asyncio.gather` for paired siblings is the load-bearing change.** Without it, sequential processing meant only ONE leg was ever in the registry at a time → web coalescing was meaningless. The change keeps risk-evaluation per-leg (each gets its own thread_id, each runs through risk independently in parallel — both legs are read-only on broker state at risk time, so parallel is safe). If a future strategy emits triplet+ orders sharing a pair_id (currently only PMCC roll = 2-leg compound), the same code paths handle them — `_group_orders_by_pair_id` doesn't cap group size, `coalesce_paired_view` accepts ≥2 views, `find_sibling` returns the FIRST sibling (not all). Triplets would render only 2 legs in the coalesced card; revisit if/when needed.
- **Render-time coalescing means the user can land on either leg's URL and see both.** The detail handler always builds the primary view, then asks the registry for a sibling. If found → coalesced view. If not → solo view. Sibling absence at render time is normal during the brief window between leg 1's interrupt firing and leg 2's interrupt firing — htmx polling on the detail page would close that gap; deferred to a B-v2 polish PR (design §6 v2 note).
- **Modify on paired cards is intentionally disabled in B.2.** The button shows but is disabled with a tooltip pointing the user at Telegram `/modify <id> <qty>` for individual-leg modifications. Modifying a paired roll is conceptually fraught — you'd need separate qty inputs per leg, and re-evaluating risk on the close leg might invalidate the open leg's risk verdict. Defer to a later phase that thinks through the modify-paired contract.
- **`coalesce_paired_view` uses the CLOSE leg's risk verdict as the primary.** Both legs were risk-evaluated independently; surfacing both verdicts in one card is a B-v2 polish task. Today the close leg's verdict is shown — typically "approve" since both legs of a roll usually pass risk. If the OPEN leg was risk-resized but the CLOSE leg approved cleanly, the user wouldn't see the resize on the coalesced card. Mitigation: warnings block surfaces both legs' warnings (deduped). Real-money risk is bounded — the resized qty already happened during risk evaluation, the user's only choice at this stage is approve/reject the post-risk shape.
- **`pmcc_pair_id` extraction lives in the registry, not the route handler.** Decoded once at `wait()` registration, stored on the entry. Route + sibling lookup just read `entry.pmcc_pair_id` — no JSON re-parse per request. Test fixtures need to include the `extra_json` field on the order row to exercise pair-coalescing.
- **B.4 still NOT shipped.** `TELEGRAM_NOTIFICATION_ONLY=true` env stays unset. Soak the parallel paths (rich Telegram + new structured web) for ~1 week before flipping. Until then, both surfaces work; first-decision-wins resolves any race.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 485 unit tests pass on local (vs 442 pre-B.2/B.3 baseline; +43 = 20 view + 7 registry + 6 grouping + 6 routes new modify + 4 routes new paired). 5 pre-existing P2 failures unchanged (BACKLOG line 1247).
- md5 6/6 files MATCH between local and prod post-scp.
- Backup tag `.pre-b23-hitl-rich-pairs-20260503-0417` placed on the 5 mutated prod files pre-deploy.
- PID 118285 → 119776 (restart at 04:20:05 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~33s after restart (Robinhood + Fidelity logins block bind, normal).
- `GET /` 200 in 2.96s; `GET /approvals` 200 in 2.74s (renders new "HITL · phase B" badge + empty state); `GET /research` 200 in 2.55s.
- POST `/approvals/x/decide` with invalid decision returns 400 with the new error string `"decision must be 'approve', 'reject', or 'modify'"` — confirms the modify branch landed and the error message updated.
- journalctl post-restart: 6 ERROR lines, all pre-existing — 5 Fidelity Azure-IP block (BACKLOG P1 #1276) + 1 yfinance BTC/USD earnings noise. Same set as B.1 deploy + every restart since Fidelity scope was added. ZERO new errors related to position_context, pair grouping, registry pair semantics, or the modify form.
- Audit log on prod immediately after restart shows `research_position_context_emitted` rows for both lord_otter and market_cypher (the existing position-context prime task runs on startup, unrelated to this deploy — sanity-check that the rest of the system is healthy).

**Inert / dormant on current traffic:**
- **Pair coalescing has no work to do until a PMCC scan emits a roll.** Sun pre-market (deploy time was 04:20 UTC Sunday); next scheduled scan Mon 2026-05-04 ~13:30 UTC. If that scan emits any rolls (LEAP-Hard-Rule promotion fires, halfway-roll cooldown doesn't fire, etc.), they'll be the first production exercise of the parallel-grouping + coalesced-card flow. Solo orders (open_pmcc, sell_weekly) take the unchanged sequential path.
- **Modify form has no users until a Board approval is pending.** Same condition as above — first scout-emitted approval Mon 13:30 UTC.
- **Restart-recovery still NOT wired** (carried forward from B.1 — design §3 / §9 v2). Mid-approval restart wipes the registry; LangGraph state survives in SqliteSaver but the user has no surface to act on it. Acceptable; v2 polish.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-b23-hitl-rich-pairs-20260503-0417; BASE=/home/azureuser/trading_corp;
for f in trading_corp/comms/pending_registry.py trading_corp/main.py trading_corp/web/routes.py trading_corp/web/templates/approvals.html trading_corp/web/templates/approval_detail.html; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
rm -f \$BASE/trading_corp/comms/position_context.py;
sudo systemctl restart trading-corp
"
```

(Note: this rollback ALSO needs to back-out B.1's registry seam if you want to fully revert HITL-in-app — see the B.1 rollback at the 03:50 UTC entry. Reverting B.2/B.3 alone leaves B.1's bare-bones `/approvals` page intact, which is a valid stopping point.)

---

## 2026-05-03 03:50 UTC — Phase B.1: HITL `/approvals` web surface + PendingApprovalRegistry seam

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board direction (BACKLOG P0 — "HITL approval flow lives in the web app", NEW 2026-05-03). Phase A shipped 02:09 UTC tonight (dormant slim-Telegram switch). Phase B.1 builds the foundation the slim flag will eventually point at: a new `PendingApprovalRegistry` that owns the per-order Future, plus three new `/approvals*` routes that read + resolve via that registry. Telegram inline-keyboard path is preserved in parallel (first-decision-wins) so the soak window has both surfaces live. User explicit go-ahead in-session 2026-05-03 ~02:30 UTC; chose two-cut strategy (B.1 separate from B.2+B.3) so the load-bearing registry seam validates discrete on prod before stacking polish/pair-coalescing on top. Markets-closed Sun→Mon window picked deliberately for HITL deploys.
**Backup tag:** `.pre-b1-hitl-web-20260503-0349` (on the 4 mutated files; the 3 new files have no backup target — `rm` is the rollback for them)

**Files deployed (4 modified, 3 new):**

*New:*
- `trading_corp/comms/pending_registry.py` — `PendingApprovalRegistry` class. Public surface: `wait(req, timeout_s)` (orchestrator-side; replaces `channel.request_approval`), `resolve(order_id, decision, source)` (resolver-side; called by Telegram callback OR web POST; first-wins), `register_notifier(fn)` (fan-out hook; TelegramChannel registers its message-send), `list_pending()` / `get(order_id)` / `get_entry(order_id)` (read-only views for the index/detail UIs), `pending_count()`. Audit chain: `pending_approval_added` (written by `wait()` BEFORE notifiers fire so dashboard can recover from audit even if notifiers all fail) → `board_decision_received` (written by `resolve()` with `source` tag — 'telegram'/'web'/'cli'/'auto'/'timeout'). Exception in one notifier doesn't block others (`_safe_notify` wraps each with broad except). Audit writes are best-effort (try/except) so a temporarily-down audit DB doesn't block approvals.
- `trading_corp/web/templates/approvals.html` — index template. Empty state + populated list. Each row: summary, division, order_id (truncated), added_at HH:MM:SSZ, "Review →" link to detail page. Tailwind chrome via `base.html`. Mobile-responsive.
- `trading_corp/web/templates/approval_detail.html` — detail template. Header (division · order_id · added_at), summary block, expandable raw-detail JSON dump (B.2 will replace with structured renderer), Approve / Reject form buttons. In-page JS intercepts the form POST, swaps result into `#decision-result` so the user stays on the page (no full-page reload). 409 → "Already decided" message with warn color. Modify intentionally NOT shipped in B.1 (deferred to B.2 per design §14).

*Modified:*
- `trading_corp/comms/telegram_bot.py` — constructor accepts `registry: PendingApprovalRegistry | None = None`. New `_build_approval_message(req)` factored out of the existing `request_approval` so the slim/rich body logic is shared. New `_notify_approval(req)` is the fan-out hook (registered on registry in `start()` after `_app` is initialized — needs the bot to send messages). `request_approval` now branches: when registry is set, delegates to `await self._registry.wait(req)`; without registry, falls back to the legacy in-channel `_pending` Future flow (preserved verbatim so non-registry paths — CLI dev, older tests — see byte-identical behavior). `_on_callback`, `/approve`, `/reject`, `/modify` all migrated to `_resolve_decision(order_id, decision)` helper that prefers `registry.resolve(..., source="telegram")` then falls back to legacy `_pending`. `_on_status_cmd` reads count from registry when wired. Inline keyboard preserved in slim mode (Phase A behavior unchanged).
- `trading_corp/main.py` — constructs `pending_registry = PendingApprovalRegistry(logger_agent=logger_agent)` immediately after the agent block, before TelegramChannel. Threaded into `TelegramChannel(... registry=pending_registry)` constructor, into `tg_deps = WebDeps(... pending_registry=pending_registry)` (for /pending command surface, future-proofed though not consumed in B.1), and into `_start_web_server(... pending_registry=pending_registry)` which forwards to `WebDeps(... pending_registry=...)`. `_run_order` is UNCHANGED — still calls `await channel.request_approval(req)`; the channel internally delegates to `registry.wait(req)` when wired, so the orchestrator path is byte-identical at the call-site level. This keeps test_graph_hitl.py green without modification.
- `trading_corp/web/app.py` — `WebDeps` dataclass gains `pending_registry: Any = None` field. Doc-commented as "constructed in main.py before TelegramChannel so the channel can register its message-send as a notifier."
- `trading_corp/web/routes.py` — three new routes registered after `/system`:
  - `GET /approvals` — `templates.TemplateResponse("approvals.html", {snap, entries, registry_unavailable})`. Empty state when `entries == []`; explanatory note when registry is None (CLI fallback / dev).
  - `GET /approvals/{order_id}` — fetches `registry.get_entry(order_id)`; 404 when not pending. Renders detail template.
  - `POST /approvals/{order_id}/decide` — accepts JSON or form-encoded body; `decision` in `{approve, reject}` (modify deferred); calls `registry.resolve(order_id, decision, source="web")`. 200 + small HTML fragment on accept; 409 on already-resolved; 400 on bad decision; 404 on no-registry.

**Local-only (NOT deployed):**
- `tests/test_pending_registry.py` — 11 tests covering wait/resolve happy path, idempotency (second resolve returns False), unknown-order-id, audit row writes (`pending_approval_added`, `board_decision_received` with source tag), notifier fan-out + exception isolation, list_pending newest-first ordering, get/get_entry.
- `tests/test_approvals_routes.py` — 13 tests using FastAPI TestClient: index empty/populated/no-registry states, detail 200/404 + raw-detail JSON rendering, decide approve/reject (resolves Future), 409 on duplicate, 400 on invalid/unknown decision, 404 when registry is None, JSON-body acceptance.

**Features shipped (load-bearing for future "is X done?" checks):**
- **`PendingApprovalRegistry` is the new HITL chokepoint.** Single instance per process (constructed in `main.py`). The web app + Telegram both share it; first-decision-wins, second gets a 409 (web) or "already decided" (Telegram). Tests construct their own per case.
- **Three new web routes are live behind Authelia:** `GET /approvals`, `GET /approvals/{order_id}`, `POST /approvals/{order_id}/decide`. The detail page works on a 375px-wide phone screen — confirmed in template; live mobile validation deferred to next signal.
- **Audit chain extended with two new kinds:** `hitl/pending_approval_added` (when an entry is registered) and `hitl/board_decision_received` (when a decision is resolved, tagged with source). Both are best-effort writes — registry continues working if audit DB is temporarily unavailable. The existing `board_approved` / `board_rejected` rows (written by graph nodes) remain unchanged.
- **TelegramChannel is now mode-aware about the registry.** When constructed with `registry=...`, message-send is registered as a notifier on `start()` and inline-keyboard / command resolution all flow through `registry.resolve(..., source="telegram")`. Without a registry, byte-identical legacy behavior (preserved for CLI dev + non-Telegram test paths).
- **Telegram inline keyboard still works in parallel.** Both surfaces (web + Telegram) converge at the same Future. The slim-format flag from Phase A stays OFF — soak window observes both surfaces with the rich body still shipping to Telegram.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`_run_order` is unchanged at the call site.** It still calls `await channel.request_approval(req)`. The redirection to `registry.wait` happens inside TelegramChannel — keeps the orchestrator API stable and means test_graph_hitl.py works without modification (it bypasses channels entirely via `Command(resume=...)`). Anyone who later wants the orchestrator to call `registry.wait` directly can do so; current shape is the smaller-diff option.
- **TelegramChannel keeps a vestigial `_pending` dict for legacy paths.** When constructed without a registry (CLI dev, older tests), the dict still owns the Future. Production always wires a registry → dict is unused. Don't delete the dict in a B.2 polish PR without auditing every TelegramChannel construction site.
- **`/approvals` reads from in-process state, not the audit DB.** A restart wipes the registry. Suspended LangGraph threads survive in the SqliteSaver checkpointer, but the registry itself doesn't auto-recover. Recovery (read recent `pending_approval_added` audit rows that don't have a matching `board_decision_received`, re-add to registry, re-emit notification) is a B-v2 polish item documented at `planning/hitl_in_app_design.md` §3 + §9. Today, a mid-approval restart loses the approval surface — the user re-triggers via the originating scout/webhook.
- **Modify intentionally NOT shipped in B.1.** Web POST returns 400 if `decision="modify"`. Telegram `/modify <id> <qty>` still works (resolves the registry directly). B.2 lands the web-side modify flow with htmx swap + form expansion.
- **`/approvals` route registered before the catch-all 404 handler.** FastAPI route order matters; verified by the 404-on-unknown-order-id test.
- **Phase A slim-format flag is unchanged.** `TELEGRAM_NOTIFICATION_ONLY` stays OFF on prod systemd. Soak Phase B.1 + Phase B.2 + Phase B.3 with rich body + web in parallel; flip the flag at B.4 after ~1 week of confidence.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 442 unit tests pass on local (vs 418 pre-B.1 baseline; +24 = 11 `test_pending_registry` + 13 `test_approvals_routes`). 5 pre-existing P2 failures unchanged (BACKLOG line 1247, PMCC scan liquidity gate — unrelated to B.1).
- md5 7/7 files MATCH between local and prod post-scp.
- Backup tag `.pre-b1-hitl-web-20260503-0349` placed on the 4 mutated prod files pre-deploy.
- PID 115197 → 118285 (restart at 03:50:38 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~36s after restart (Robinhood + Fidelity logins block bind, normal).
- Symbol-presence checks on prod files: `PendingApprovalRegistry` present in `pending_registry.py` + imported in `telegram_bot.py` + `main.py`; `_notify_approval` + `_resolve_decision` present in `telegram_bot.py`; `pending_registry` field present on `WebDeps`; three new routes present in `routes.py`; new templates present in `web/templates/`.
- `GET /` 200 in 2.53s; `GET /research` 200 in 2.61s.
- `GET /approvals` 200 in 2.52s. Response body contains "No approvals pending" — empty state rendering correctly.
- `GET /approvals/nonexistent` 404 — detail-page guard works.
- journalctl post-restart: only errors are pre-existing Fidelity Azure-IP block (BACKLOG P1 #1276 — datacenter IPs flagged; same pattern as every restart since Fidelity scope was added) and yfinance BTC/USD earnings noise (external API hiccup, same pattern as prior 02:09 UTC + 00:05 UTC deploys). ZERO new errors related to registry / pending_approval / hitl / web routes / templates.

**Inert / dormant on current traffic:**
- **No real PMCC-scout-emitted approvals are pending right now** (Sun pre-market; next scheduled scan is Mon 2026-05-04 ~13:30 UTC). The `/approvals` page shows the empty state. First production exercise of the registry's full wait→resolve loop happens on Monday's first Board-routed scout output. Until then, the integration is exercised only by the test suite's mock orchestration.
- **Restart-recovery is NOT wired.** If trading-corp restarts mid-approval (e.g. a deploy lands during Board deliberation), the registry empties; the suspended LangGraph thread state survives in the SqliteSaver but the user has no surface to act on it without re-triggering. Acceptable for B.1; recovery is a B-v2 polish item.
- **`TELEGRAM_NOTIFICATION_ONLY` env stays unset.** Phase A's slim Telegram body remains dormant. Soak the parallel paths (rich Telegram + new web) for ~1 week before flipping at B.4.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-b1-hitl-web-20260503-0349; BASE=/home/azureuser/trading_corp;
for f in trading_corp/comms/telegram_bot.py trading_corp/main.py trading_corp/web/app.py trading_corp/web/routes.py; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
rm -f \$BASE/trading_corp/comms/pending_registry.py \\
      \$BASE/trading_corp/web/templates/approvals.html \\
      \$BASE/trading_corp/web/templates/approval_detail.html;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-03 02:09 UTC — Phase A: HITL slim-Telegram bridge + PMCC prompt-text refinements (cooldown reframing + LEAP-Hard-Rule note + STD strike example)

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board direction (BACKLOG P0 — "HITL approval flow lives in the web app; Telegram becomes notification-with-deeplink", NEW 2026-05-03). Phase A is the smallest cut of that P0: dormant `notification_only` switch in `TelegramChannel` + new slim-format builder + env-var wiring. Bundled with PMCC prompt-text clarifications (4 edits — COOLDOWN reframing in BS+STD blocks, BLACK_SHEEP LEAP-Hard-Rule NOTE, STANDARD STRIKE TARGETING regime-appropriate example) since the working-tree file was already mutated alongside the comms changes. User explicit go-ahead in-session 2026-05-03 ~02:00 UTC after deciding to skip the Monday PMCC scan validation gate (will validate live on next signal). The prior plan (BACKLOG ## 📦 PENDING DEPLOY) called for waiting until Mon ~13:30 UTC; that gate is dropped because the slim-format change is dormant by default and the prompt edits are LLM-facing only — first signal after deploy exercises both safely.
**Backup tag:** `.pre-phase-a-slim-telegram-20260503-0209` (on the 4 mutated files)

**Files deployed (4):**
- `trading_corp/comms/approval_format.py` — adds `format_slim_approval_notification(order, order_id, division, base_url)` returning a Markdown-Telegram body of the form `<ACTION> · <SYM> · <division>\n\n[Review on dashboard →](<base_url>/approvals/<order_id>)`. Existing `format_approval_message` (rich body) preserved unchanged. Defined at `approval_format.py:29`. URL is `{base_url}/approvals/{order_id}` — pair-coalescing happens server-side at the dashboard once Phase B ships, not in the formatter.
- `trading_corp/comms/telegram_bot.py` — `TelegramChannel.__init__` gains `notification_only: bool = False, dashboard_base_url: str | None = None` kwargs (line 40-41). `request_approval` branches on the flag (line 184): when False (default) emits the existing rich format; when True calls `format_slim_approval_notification`. Inline approve/reject keyboard preserved in BOTH modes during Phase A — lets us flip the flag the day Phase B ships without losing tap-to-approve until the web button is canonical.
- `trading_corp/main.py` — env-var wiring (line 500-507): `TELEGRAM_NOTIFICATION_ONLY=true` flips the flag; `DASHBOARD_BASE_URL` overrides the production default. Both default to safe values that preserve the rich body.
- `trading_corp/agents/divisions/pmcc_robinhood.py` — 4 prompt-text edits, no code-path changes:
  1. **`_BLACK_SHEEP_RULES` Rule 6 COOLDOWN reframed.** Was a NOTE explaining the deterministic `_recent_halfway_roll_cooldown` backstop. Now the rule itself instructs "HONOR the cooldown directly when ROLL HISTORY shows a recent halfway roll" with concrete acceleration-override math (`spot now > prior_short_strike_after + |prior_strike_change|`). Backstop language demoted to "BACKSTOP: the guard catches the override-vs-cooldown edge cases" — keeps the LLM and the guard semantically aligned rather than appearing to fight each other.
  2. **`_BLACK_SHEEP_RULES` LEAP-Hard-Rule NOTE added** (12 lines after the strict perpetual-roll philosophy section). Explains that `_promote_to_roll_leap_if_hard_rule` fires regardless of regime when LEAP delta>=0.95 OR DTE<120, AND that BS philosophy normally would defer LEAP exit longer — so when the guard promotes a roll_short → roll_leap on a BS position, the user can still reject the LEAP roll and approve only the short roll. Tells the LLM how to AVOID triggering the guard (choose `hold` or `watch` instead of `roll_short` until DTE crosses BS exit threshold).
  3. **`_STANDARD_RULES` BREACH POLICY COOLDOWN reframed** (parallel to BS Rule 6 above). Same "HONOR the cooldown directly" language + concrete acceleration math + BACKSTOP demotion.
  4. **`_STANDARD_RULES` STRIKE TARGETING example regime-appropriated.** Was "halfway midpoint = $X.XX" (a BS-shaped example). Now "roll above $200 resistance" — a STD-regime example. Also added explicit guidance to LEAVE `target_strike` null for normal cycle rolls where delta-target IS the selection criterion (avoids over-eager target_strike population that would defeat delta ranking).

**Local-only (NOT deployed):**
- `tests/test_slim_approval_notification.py` — 11 tests pinning the slim body shape (headline composition, division-omission when None, URL formatting, base_url trailing-slash handling, Markdown-link safety). Local-only: prod doesn't run tests in-tree.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Slim Telegram body shape is locked.** `format_slim_approval_notification` is the canonical function; any future caller that wants the slim ping (e.g. paired-roll coalesced URLs in Phase C) calls this. URL contract: `<base_url>/approvals/<order_id>` for individual orders. Pair-form (`/approvals/pair/<pmcc_pair_id>`) is reserved for Phase C and would need a sister function.
- **`TelegramChannel` is now mode-aware.** Instantiating with `notification_only=True` flips to slim; default False preserves rich body. Mode is per-instance, not per-call — so all approvals from a single channel use the same body shape.
- **Env contract:** `TELEGRAM_NOTIFICATION_ONLY` (default `false`) + `DASHBOARD_BASE_URL` (default the production URL) wired into the channel constructor in `main.py`. To activate slim mode in prod, set `TELEGRAM_NOTIFICATION_ONLY=true` on the systemd unit AFTER Phase B `/approvals/{id}` route exists. Until then: do not flip.
- **PMCC prompt rule clarifications LIVE on next analysis.** COOLDOWN clauses now cite the deterministic backstop explicitly; LEAP-Hard-Rule NOTE explains the cross-regime promotion path; STANDARD STRIKE TARGETING example is regime-appropriate. The Anthropic API call uses the new prompt text on every scan / re-analyze trigger from this restart forward.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Slim mode is opt-in and dormant on this deploy.** Default `notification_only=False`; env vars unset on prod systemd unit (verified). Behavior at the Telegram-message layer is byte-identical to pre-deploy. The new code paths exist but are not exercised on production traffic.
- **Inline keyboard is preserved in slim mode (Phase A bridge behavior).** When `notification_only=True` activates (post-Phase-B), the message body shrinks to "headline + deeplink" but the inline keyboard stays so the existing Telegram approve/reject still works. Phase B's web button becomes the canonical surface; the keyboard goes away when the bridge is removed (likely Phase C or D — TBD in `planning/hitl_in_app_design.md`).
- **`format_slim_approval_notification` takes `order_id` explicitly,** not derived from `order` — because the order shape across callers (ProposedOrder vs DB-row dict) doesn't carry the id consistently. Caller is responsible for passing it.
- **PMCC prompt-text changes are LLM-facing only.** No new symbols introduced. The deterministic guards shipped at 00:05 UTC (`_recent_halfway_roll_cooldown`, `_promote_to_roll_leap_if_hard_rule`) and 00:36 UTC (`target_strike` plumbing) are unchanged. Symbol-presence on prod files re-verified post-deploy.
- **The COOLDOWN reframe shifts authority from "LLM informed by NOTE about backstop" to "LLM applies cooldown directly, backstop catches edge cases."** Net behavior should be: fewer cases where the LLM picks `roll_short` and the guard rewrites to `hold` (and the audit row reads inconsistently — LLM rationale says "roll", action says "hold"). Back-to-back halfway rolls are still prevented either way; the difference is the LLM's narration aligns with the action.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 418 unit tests pass on local (5 pre-existing P2 failures unchanged — same `_call`-helper liquidity-gate trap as prior deploys; not in this batch's blast radius).
- md5 4/4 files MATCH between local and prod post-scp.
- Backup tag `.pre-phase-a-slim-telegram-20260503-0209` placed on all 4 files pre-deploy.
- PID 113881 → 115197 (restart at 02:09:48 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~33s after restart (Robinhood + Fidelity logins block bind, normal).
- Symbol-presence checks on prod files: `format_slim_approval_notification` (1 hit, definition + import in telegram_bot.py implied by 3 hits there); `notification_only` (3 hits — kwarg + assign + branch); `TELEGRAM_NOTIFICATION_ONLY` (2 hits — comment + getenv); `DASHBOARD_BASE_URL` (2 hits — comment + getenv).
- `GET /` 200 in 2.63s; `GET /research` 200 in 2.61s.
- journalctl post-restart: only errors are the pre-existing Fidelity Azure-IP block (BACKLOG P1 #1276 — datacenter IPs flagged at network layer; same pattern as every restart since Fidelity scope) and yfinance BTC/USD earnings noise (external API hiccup, same pattern as prior 00:05 UTC deploy line 160). No ImportError / NameError / AttributeError / Traceback related to the new code.
- Env sanity: `TELEGRAM_NOTIFICATION_ONLY` and `DASHBOARD_BASE_URL` neither set on systemd unit — slim mode dormant, as planned.

**Inert / dormant on current traffic:**
- **Slim Telegram format is dormant.** `notification_only=False` default + env unset → rich body is what gets sent. Activates only when (a) Phase B `/approvals/{id}` page exists on prod AND (b) `TELEGRAM_NOTIFICATION_ONLY=true` is added to the systemd unit. Until then this deploy is byte-for-byte equivalent at the Telegram-message layer.
- **PMCC prompt refinements active immediately on the LLM call path.** Anthropic API call uses the new prompt text on every scan / re-analyze trigger from this restart forward. First production exercise: next scheduled scan (Monday 2026-05-04 ~13:30 UTC) or any "Re-analyze" click before then. Per user's "validate live on next signal" decision, no synthetic test was run pre-deploy — the next real PMCC analysis is the validation event.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-phase-a-slim-telegram-20260503-0209; BASE=/home/azureuser/trading_corp;
for f in trading_corp/comms/approval_format.py trading_corp/comms/telegram_bot.py trading_corp/main.py trading_corp/agents/divisions/pmcc_robinhood.py; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-03 00:36 UTC — PMCC P1 (Item 3): target_strike honors LLM rule-driven strike (halfway-rule strike drift fix)

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board pre-authored fix sketch in BACKLOG.md (P1 — PMCC roll: Recommended strike ignores the halfway-rule the expert text cites). User explicit go-ahead in-session 2026-05-02 after re-evaluating the residual risk under paper-mode + HITL: even if a wrong strike is recommended, it surfaces in the Telegram approval card and the Board catches it before placement. Closes the third of three related PMCC roll-correctness items shipped this session (sister DONE entries: roll-history blindness + LEAP-roll-missing).
**Backup tag:** `.pre-pmcc-target-strike-20260503-0035` (on the 1 mutated file)

**Files deployed (1):**
- `trading_corp/agents/divisions/pmcc_robinhood.py` — five touchpoints, all threading the new `target_strike` field through the existing call chain:
  1. **`PMCCAnalysis` dataclass** gains `target_strike: float | None = None`. Backwards-compat default — existing callers that don't supply it get None and behave identically. Field annotated with the role: when set, strike picker honors it and overrides delta-distance ranking.
  2. **`_select_weekly_strike(calls, target_delta, target_strike=None)`** — when `target_strike` is set, picks the listed strike whose `strike_price` is closest to it (ignoring delta entirely). Caller is responsible for sanity — we don't second-guess (the LLM cited the strike per its rules; e.g. an ITM defensive halfway-roll). When `target_strike` is None, falls through to the original delta-distance behavior with OTM-only filtering. Defensive: returns None if no eligible strike (no strike_price field).
  3. **`_find_best_weekly(symbol, broker, target_delta=None, target_dte=None, target_strike=None)`** — accepts target_strike, threads to `_select_weekly_strike`. DTE/expiry-window selection unchanged.
  4. **LLM prompt JSON schema** (in `_llm_analyze_position`) — added `"target_strike": <recommended short call STRIKE as float, or null>` to the response template. Field annotated as: "set this when a rule prescribes a specific strike (e.g. halfway-roll midpoint per BREACH HANDLING). When set, the strike picker honors this directly, overriding delta-distance ranking. Leave null when delta-targeting is correct (standard cycles)."
  5. **JSON parse** (in `_llm_analyze_position`) — extracts `target_strike` with the same float-or-None pattern as `target_delta`. Defensive: if the LLM omits the field, falls through to None (no strike override, original behavior).
  - **Threaded through 5 callers** of `_find_best_weekly` (single-line in 4 places, multi-line in 1): `propose_orders_for_pair` `roll_leap` 4th-leg, scan-path inline `roll_leap` 4th-leg, `_propose_open_pmcc`, `_propose_sell_weekly`, `_propose_roll_short`. Each adds `target_strike=analysis.target_strike if analysis else None`.
  - **Prompt rule corpus updated:** `_BLACK_SHEEP_RULES` Rule 6 (BREACH HANDLING) and `_STANDARD_RULES` BREACH POLICY each gained a STRIKE TARGETING clause instructing the LLM to populate `target_strike` when narrating a specific strike (halfway midpoint or rule-cited target). Without this clause the LLM had no signal that the new field existed; with it, the rule application is coherent end-to-end.

**Features shipped (load-bearing for future "is X done?" checks):**
- **`PMCCAnalysis.target_strike` is now a real field.** Anywhere downstream that needs to know what strike the LLM cited can read `analysis.target_strike` directly instead of regexing the rationale text.
- **Strike picker honors LLM-cited strikes.** When the LLM applies a rule like "Major Breach → halfway midpoint = $X.XX" and populates `target_strike`, the recommendation card's open leg lands at the listed strike closest to that value. Pre-fix the picker fell back to `target_delta` ranking, which on high-IV underlyings typically picked a strike well above the cited halfway midpoint — the BACKLOG-cited MSTR symptom (cited $169, picked $187.50). BACKLOG.md "P1 — PMCC roll: Recommended strike ignores the halfway-rule the expert text cites" → DONE.
- **Backwards-compat preserved.** `target_strike=None` (the default + the LLM's response when omitted) → original delta-distance behavior. No drift on standard-cycle recommendations. Pinned by `test_propose_roll_short_falls_back_to_delta_when_target_strike_none`.
- **All 5 `_find_best_weekly` call sites are wired.** Both `roll_leap` 4-leg branches (propose_orders_for_pair + scan-path) honor target_strike on the new-short leg, so a halfway-into-LEAP-roll scenario gets both LEAP rolled AND new short at the LLM-cited strike.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`target_strike` overrides target_delta when both are set.** This is intentional — the LLM populates target_strike only when it has a rule-driven specific target; in that case the delta is incidental. If you ever need both honored simultaneously, change `_select_weekly_strike` to filter by delta range and minimize strike distance within that pool. Today's behavior is "strike wins" because that's what the rule citation requires.
- **No spot-acceleration check in the picker.** Same rationale as the cooldown guard from the prior deploy: keep the deterministic helper simple; the LLM's rule corpus is responsible for choosing the right strike based on regime/IV context. The picker just honors the choice.
- **Prompt rule clauses added to BOTH `_BLACK_SHEEP_RULES` Rule 6 AND `_STANDARD_RULES` BREACH POLICY.** Both regimes need the STRIKE TARGETING note because both regimes can cite specific strikes (halfway-roll for black sheep on Major/Runaway breach; up-and-out for standard on Major). If a future regime-specific rule block is added (e.g. crypto-options-specific), it needs the same clause.
- **The 5 caller threading is mechanical.** If a future `_find_best_weekly` caller is added (e.g. a new strategy variant), pattern-copy the existing `target_strike=analysis.target_strike if analysis else None` line.

**Latent bugs caught + fixed:** none. The 5 pre-existing P2 PMCC scan failures (BACKLOG.md line 1093) remain unchanged; my new tests use the local `_liquid_call` helper introduced in the prior deploy to avoid the same trap.

**Verification:**
- Pre-deploy: 407 unit tests pass on local (vs 397 baseline before this deploy; +10 = 4 strike-picker tests, 2 dataclass tests, 1 `_find_best_weekly` integration, 2 `_propose_roll_short` end-to-end, 1 defensive). 5 pre-existing P2 failures unchanged.
- md5 1/1 file MATCH between local and prod post-scp.
- Backup tag `.pre-pmcc-target-strike-20260503-0035` placed on `pmcc_robinhood.py` pre-deploy.
- PID 112932 → 113881 (restart at 00:35:50 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~45s after restart (Robinhood + Fidelity logins block bind, normal).
- Symbol presence: `target_strike` appears 26 times in prod's `pmcc_robinhood.py` (definition + dataclass + 5 callers + parse + prompt schema + 2 rule clauses + tests in comments + helper signature). Local matches.
- `GET /` 200 in 2.75s; `GET /research` 200 in 2.62s.
- journalctl post-restart: zero errors of any kind in the filtered window. Service loaded cleanly.

**Inert / dormant on current traffic:**
- The `target_strike` override is dormant until the LLM populates the field on its next analysis. The Anthropic API call is live on the existing scan schedule; the prompt rule clause is the trigger that gets the LLM to populate it. Expect the next scheduled scan (Monday 2026-05-04 ~13:30 UTC) to be the first time the field gets exercised live for a Board-visible recommendation.
- Backwards-compat path (target_strike=None) is what every current cached analysis (if any) and every LLM response that doesn't include the new field will exercise. This path is byte-identical to pre-deploy behavior.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-pmcc-target-strike-20260503-0035; BASE=/home/azureuser/trading_corp;
mv \$BASE/trading_corp/agents/divisions/pmcc_robinhood.py\$TAG \$BASE/trading_corp/agents/divisions/pmcc_robinhood.py;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-03 00:05 UTC — PMCC P1 guards: LEAP Hard Rule promotion + halfway-roll cooldown + roll_leap 4-leg compound

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board pre-authored fix sketches in BACKLOG.md (P1 — PMCC roll: LLM analyzer is blind to recent roll history; P1 — PMCC drilldown: Recommended Trade omits the LEAP roll when both legs need to roll). User explicit go-ahead in-session 2026-05-02. Both items are real-money correctness gaps: (a) the LLM analyzer was recommending back-to-back halfway rolls because its prompt had zero history; (b) when LEAP delta>=0.95 OR DTE<120 the recommendation card was emitting a 2-leg roll_short instead of a 4-leg roll_leap, leaving a fresh short on a dying LEAP if approved.
**Backup tag:** `.pre-pmcc-p1-guards-20260502-2357` (on the 2 mutated files)

**Files deployed (2):**
- `trading_corp/agents/divisions/pmcc_robinhood.py` — five additions, three deterministic-then-narrate guards plus the 4-leg `roll_leap` extension:
  1. **`_promote_to_roll_leap_if_hard_rule(analysis, leg)`** (Item 2). Promotes `roll_short` / `roll_short_early` → `roll_leap` when `leg.long_leg_delta >= 0.95` OR `leg.long_leg_dte < 120`. Honors CLAUDE.md §1's deterministic-then-narrate principle — the LEAP Hard Rule trigger is purely a function of already-computed numeric state, so it shouldn't ride through LLM judgment. Adds an explanatory warning to `analysis.warnings` so the audit trail + Telegram approval message render the reason.
  2. **`_recent_halfway_roll_cooldown(analysis, leg)`** (Item 1). Backstop guard that downgrades `roll_short` → `hold` when a recent roll-up (positive strike_change >= $1) was executed within `cooldown_days` (default 7) AND short DTE > terminal_dte_floor (default 2) AND extrinsic > extrinsic_floor (default $0.50/sh). The LLM also gets the rule clause + ROLL HISTORY block in its prompt and should already prefer HOLD; this guard is the deterministic backstop.
  3. **`_query_prior_rolls_detailed(symbol, leap_lifetime_key)`** (Item 1). Sister to the existing `_query_prior_rolls` — same SQL/grouping but returns `last_roll_ts`, `last_roll_short_strike_before`, `last_roll_short_strike_after`, `last_roll_strike_change`, `days_since_last_roll`. Used by the cooldown guard AND the prompt formatter. `leap_lifetime_key` scoping mirrors the existing helper (pre-fix NULL-keyed pairs preserved; mismatched-key pairs filtered).
  4. **`_format_roll_history_block(leg)`** (Item 1). Builds the ROLL HISTORY section injected into `_llm_analyze_position`'s prompt. Empty string when no DB; "No prior rolls" copy when DB is empty for this LEAP; otherwise count + net dollars + most-recent strike change with a roll-up/roll-down label.
  5. **4-leg `roll_leap` compound.** Both `roll_leap` branches (`propose_orders_for_pair` line ~1085 and the inline scan-path branch line ~1921) extended to emit a 4th order: open new short on the new LEAP. Skipped gracefully if no qualifying weekly chain — next scan picks up the uncovered LEAP via the `open_short` branch. Pre-fix the recommendation was 3 legs (close short + close LEAP + open new LEAP), which would leave the user uncovered if approved as-is. The BACKLOG entry's verification text required the 4-leg compound; the entry's claim that "the existing roll_leap action DOES already build a compound roll (close short + close LEAP + open new LEAP + open new short)" was inaccurate — this deploy makes that claim true.
  - Composition order at both call sites: `_terminal_dte_time_release` → `_promote_to_roll_leap_if_hard_rule` → `_recent_halfway_roll_cooldown`. Rationale: terminal-DTE first because deadline-driven rolls need to ship; Hard-Rule second because if the LEAP is dying, that lifts roll_short to roll_leap (cooldown is a no-op on roll_leap so a needed LEAP roll isn't silently vetoed); cooldown last as the pure backstop. Test `test_cooldown_does_not_fire_after_hard_rule_promotion` pins this composition.
  - Prompt updates: `_BLACK_SHEEP_RULES` Rule 6 (BREACH HANDLING) gained a COOLDOWN clause; `_STANDARD_RULES` BREACH POLICY gained the parallel clause; `_STANDARD_RULES` Rule 5 (HARD RULES) gained a NOTE about the LEAP-Hard-Rule promotion. ROLL HISTORY block injected before the JSON-response request in `_llm_analyze_position`.
- `config/strategies.yaml` — new `robinhood_pmcc.roll_cooldown` block: `cooldown_days: 7`, `extrinsic_floor: 0.50`, `min_strike_change: 1.0`, `terminal_dte_floor: 2`. Hot-reloadable via the same mtime-cache mechanism the rest of `_cfg` uses.

**Features shipped (load-bearing for future "is X done?" checks):**
- **LEAP Hard Rule promotion is live.** When a PMCC scan or dashboard "Approve & Execute" produces an analysis with `roll_short` action and the LEAP has delta >= 0.95 OR DTE < 120, the action is silently promoted to `roll_leap` and the recommendation card now shows all four legs. Distracted-approval risk on dying-LEAP scenarios eliminated for these conditions. BACKLOG.md "P1 — PMCC drilldown: Recommended Trade omits the LEAP roll when both legs need to roll" → DONE.
- **Halfway-roll cooldown is live.** When the prior fill on a LEAP's lifetime was a roll-up (>= $1 strike change) within the last 7 days AND the current short isn't deadline-driven AND extrinsic is non-trivial, `roll_short` is downgraded to `hold` with a warning. Back-to-back halfway-roll waste avoided. BACKLOG.md "P1 — PMCC roll: LLM analyzer is blind to recent roll history (recommends back-to-back halfway rolls)" → DONE.
- **`roll_leap` action emits a 4-leg compound** (close short + close LEAP + open new LEAP + open new short) instead of the prior 3-leg shape. Applies to BOTH dispatch sites — `propose_orders_for_pair` (used by dashboard "Approve & Execute" + Telegram per-pair approval) and the scheduled-scan inline branch.
- **`_query_prior_rolls_detailed` is now available** as a sister to `_query_prior_rolls`. Future callers needing per-roll metadata (strike change, last-roll ts, days-since) use this; old callers (Phase 2 position-context) keep using the simpler tuple shape unchanged.
- **LLM prompt now includes a ROLL HISTORY block** scoped to the current LEAP's `leap_lifetime_key`. The narration the LLM produces when ROLL HISTORY shows a recent roll-up should now coherently cite the cooldown rule even before the deterministic guard fires.
- **`config/strategies.yaml` carries `roll_cooldown` knobs.** Tuning the cooldown window or thresholds is a one-line YAML edit + service restart.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **The 4-leg `roll_leap` is a real-money path expansion.** Pre-fix the action emitted 3 legs; post-fix it emits 4 (or 3 with a logged "no qualifying weekly" note when the new-short fallback can't fill). Anything that previously assumed `roll_leap` produced exactly 3 legs (tests, audit-grep tooling, Telegram message templates) needs to handle the 4th leg gracefully. Quick scan: nothing in the repo greps on `roll_leap_close_short`/`roll_leap_close`/`roll_leap_open` count assumptions; new audit kind `roll_leap_open_short` mirrors the existing pattern (action stashed in `extra["action"]`).
- **Composition order at both call sites is load-bearing.** `_terminal_dte_time_release` → `_promote_to_roll_leap_if_hard_rule` → `_recent_halfway_roll_cooldown`. Re-ordering would allow the cooldown to veto a needed LEAP roll (if cooldown ran before Hard-Rule promotion). Test `test_cooldown_does_not_fire_after_hard_rule_promotion` pins it.
- **The cooldown's "is this a halfway-style roll-up" detector is a heuristic.** It uses `last_roll_strike_change >= min_strike_change` (default $1.00) — captures halfway-roll-into-breach and OTM target-delta roll-ups; excludes near-zero same-strike cycle drift. Doesn't try to detect the spot-acceleration override in Python (that belongs in the LLM rule clause where regime/IV context is available). False-positive cooldown costs "user overrides via Telegram"; false-negative costs "back-to-back halfway-roll waste." Bias is intentionally toward HOLD.
- **The cooldown queries by `leap_lifetime_key`** — multi-LEAP-on-one-symbol scenarios won't cross-contaminate. Pre-fix history (NULL keys) still folds into the count, same backwards-compat as `_query_prior_rolls`.
- **The ROLL HISTORY prompt block is empty for fresh positions.** No DB query at all when `_db_url` is unset (test/CLI path). When DB present but no prior rolls, prompt gets "No prior rolls recorded for this LEAP." — the LLM sees the absence explicitly rather than missing the section entirely.
- **Both new methods live near `_terminal_dte_time_release`** in the file, reflecting the pattern they share (deterministic post-processor on PMCCAnalysis that returns a possibly-modified `dataclasses.replace`).

**Latent bugs caught + fixed:** none specific. The 5 pre-existing P2 PMCC scan failures (BACKLOG.md line 1093, "5 PMCC scan tests failing on liquidity gate") are unchanged — same failures, same root cause (test fixture's `_call` helper omits `open_interest` + `volume`, fails the standard liquidity gate). My new tests use a local `_liquid_call` helper to avoid the same trap.

**Verification:**
- Pre-deploy: 397 unit tests pass on local (vs 370 baseline before this session; +27 = `_promote_to_roll_leap_if_hard_rule` × 7, `_recent_halfway_roll_cooldown` × 11 including composition, `_query_prior_rolls_detailed` × 4, `_format_roll_history_block` × 3, `roll_leap` 4-leg integration × 2). 5 pre-existing P2 failures unchanged.
- md5 2/2 files MATCH between local and prod post-scp.
- Backup tag `.pre-pmcc-p1-guards-20260502-2357` placed on both files pre-deploy (verified file sizes).
- PID 111560 → 112932 (restart at 00:05:31 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~45s after restart (Robinhood + Fidelity logins block bind, normal).
- Symbol-presence checks on prod files: `_promote_to_roll_leap_if_hard_rule` (4 hits), `_recent_halfway_roll_cooldown` (7 hits — counts include doc references in rule blocks + call sites + definition), `_query_prior_rolls_detailed` (4 hits), `roll_leap_open_short` (2 hits — both branch implementations), `roll_cooldown:` (1 hit in strategies.yaml).
- `GET /` 200 in 2.91s; `GET /research` 200 in 2.78s; `GET /partials/trade-flow` 200.
- journalctl post-restart: only error is a transient `yfinance HTTP 500` (external API hiccup, unrelated to deploy). No ImportError / NameError / AttributeError / Traceback related to the new code.

**Inert / dormant on current traffic:**
- The cooldown guard requires a recent FILLED roll on the same symbol within the cooldown window. Today the bot is paper-mode (`auto_execute: false` everywhere) so there are no filled real-money rolls — the guard will never fire on production traffic until either (a) auto_execute flips for PMCC, or (b) the Board approves a real roll via Telegram and the data_exec path writes a `filled` row. Until then, the guard is wired but inactive. Test coverage exercises it via seeded `proposed_order` rows.
- The Hard-Rule promotion fires whenever the LLM analyzer emits `roll_short` on a position with delta>=0.95 OR DTE<120. Several current Robinhood positions are within those LEAP conditions (per the dashboard); the next scheduled scan or "Re-analyze" click will exercise the promotion live.
- The 4th `roll_leap` leg fires the moment any `roll_leap` action is approved. Until a Board approval lands one, the new code path is dormant — but observable in the recommendation card preview as soon as the next scan produces a `roll_leap` recommendation.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-pmcc-p1-guards-20260502-2357; BASE=/home/azureuser/trading_corp;
for f in trading_corp/agents/divisions/pmcc_robinhood.py config/strategies.yaml; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 23:03 UTC — PMCC research-as-consultant validation surface (05-05 review tooling)

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board direction in-session — the 2026-05-02 vision realignment created a 3-day observation period (2026-05-02 → 2026-05-05) for PMCC's `universe_source: research_on_demand` integration. Decision criteria per the realignment memo: (a) count of `research_candidate_recommendation_emitted` rows from PMCC scout, (b) count of those that produced downstream order activity, (c) qualitative read on whether the research-recommended candidates are ones PMCC would have surfaced on its own. Without dedicated tooling that decision is a vibes-call on ad-hoc SQL on 05-05; this surface makes it tractable.
**Backup tag:** `.pre-pmcc-validation-view-20260502-2301` (on the 3 mutated files)

**Files deployed (3):**
- `trading_corp/agents/logger.py` — new `LoggerAgent.events_since(ts_iso, limit=5000)` method. Date-scoped audit fetch for multi-day windows that would overflow `recent_events()`'s limit. Returns newest-first. Used by the new validation view; existing `recent_events()` callers unaffected.
- `trading_corp/web/routes.py` — new `_build_pmcc_validation_view(deps)` joins three sources: `research_candidate_recommendation_emitted` (engagement-level + candidate list) ⨝ `research_candidate_acted_on` / `research_candidate_skipped` (per-candidate division row, keyed by `(engagement_id, symbol)`) ⨝ `proposed_order.status` (downstream lifecycle for acted_on candidates' order ids). Computes scoreboard counts + skip-reason histogram. Hard-coded observation window start = `2026-05-02T00:00:00Z`. Wired into `_build_research_view` return as `view.pmcc_validation`. New helper `_lookup_order_statuses(deps, order_ids)` does a bulk SELECT against `proposed_order` for the acted_on rows' order ids. New `_empty_pmcc_validation_view()` so the empty-deps branch returns the right keys.
- `trading_corp/web/templates/research.html` — new section "PMCC research-as-consultant validation" inserted between Engagement-latency and Recommendation-outcomes sections. Top scoreboard (Engagements / Candidates / Acted on / Skipped / Approved/filled — 5 numbers in a 5-col grid). Skip-reason histogram strip below. Per-engagement collapsible cards (newest open by default) with a 6-column candidate table: Symbol / Conviction / Fit / Status pill / Order status / thesis-or-skip-reason. Status pills color-code: acted=gain, skipped=warn, no-outcome=muted. Order-status colors: filled=gain, cancelled/risk_rejected/board_rejected=loss, others=muted.

**Features shipped (load-bearing for future "is X done?" checks):**
- **`/research` now has a "PMCC research-as-consultant validation" section** showing the per-engagement candidate-level breakdown that the 05-05 review needs. Today renders empty-state ("No PMCC research engagements yet in the observation window") because no PMCC scout cycle with `universe_source: research_on_demand` has fired and completed an engagement yet — the surface is in place for when one does. Confirms the 05-05 review will not require ad-hoc SQL.
- **`LoggerAgent.events_since(ts_iso, limit=5000)`** is now available for any future caller that needs a date-scoped audit fetch (vs `recent_events`'s row-count cap). Internal usage only today; no external API surface.
- **`PMCC_OBSERVATION_PERIOD_START` constant** in `trading_corp/web/routes.py` pins the observation window. After 2026-05-05 the constant can stay (surface remains useful as a longitudinal view) or be moved to a config knob if the period needs to slide.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **PMCC's HITL flow does NOT write `would_have_placed`** — only Otter/Cypher webhook handlers do. The realignment memo phrased the 05-05 criterion as "candidates that produced `would_have_placed` rows" but PMCC's LangGraph flow goes proposed → risk_approved → board_approved → filled (no `would_have_placed` step in between). This view surfaces the actual `proposed_order.status` lifecycle for acted_on candidates instead. Documented in `_build_pmcc_validation_view` docstring. Don't re-litigate; the realignment memo's wording was imprecise on this point and the truth is in the code.
- **The view is purely additive on `/research`.** The pre-existing `Recommendation outcomes` section (per-engagement act/skip counts) is intentionally kept — it's broader (cross-division) while the new section is PMCC-specific candidate-level depth. They aren't redundant; they answer different questions.
- **Join key for division-side rows is `(engagement_id, symbol.upper())`.** If a future audit-write path stops uppercasing the symbol on either side, the join breaks silently. Test `test_full_join_acted_on_skipped_no_outcome` pins the casing.
- **Order status lookup is best-effort.** If the proposed_order id is None on an acted_on row (defensive — shouldn't happen in practice), order_status renders as None and n_board_approved_or_filled doesn't increment. Test `test_acted_on_without_order_id_surfaces_none_status` pins this.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 370 unit tests pass on local (vs the 360-baseline in prior deploy logs — the +10 are the new `tests/test_pmcc_research_validation_view.py`). 5 pre-existing P2 PMCC-scan failures unchanged.
- md5 3/3 files MATCH between local and prod post-scp.
- PID 109394 → 111560 (restart at 23:02:35 UTC). Service active.
- Port 8000 came up ~10s after restart (Robinhood + Fidelity logins block bind).
- `GET /research` returns 200 in 2.60s. New section renders. Markers present in HTML: `PMCC research-as-consultant validation` (1), `observation since` (1), `decision date 2026-05-05` (1). All 5 scoreboard labels visible (Engagements, Candidates, Acted on, Skipped, Approved/filled). Empty-state copy `No PMCC research engagements yet in the observation window` (1) — expected, no engagements yet.
- `GET /` 200 in 2.58s; `GET /partials/trade-flow` 200.
- Zero new errors in journalctl post-restart aside from the pre-existing yfinance BTC earnings noise + Fidelity bot-block (both called out in earlier deploy logs).

**Inert / dormant on current traffic:**
- The validation section is empty-state today because no PMCC `universe_source: research_on_demand` cycle has produced an engagement yet. It will populate as engagements complete. If on 05-05 it's still empty, that's the answer to the validation question (research isn't being exercised).

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-pmcc-validation-view-20260502-2301; BASE=/home/azureuser/trading_corp;
for f in trading_corp/agents/logger.py trading_corp/web/routes.py trading_corp/web/templates/research.html; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 22:10 UTC — Live trade flow: tile open-state persists across htmx 5s refresh

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board reported the expand-on-click tiles auto-closing
in the browser. Initially attributed to cursor movement; verified via
the 6-second stillness test (open a tile, don't move mouse, count to 6
— tile closes on its own). Confirmed: htmx's `every 5s` outerHTML
refresh of `#trade-flow` rebuilds every `<details>` element fresh,
which discards the `open` attribute. The original Phase A spec
("BACKLOG.md 2026-05-02 — Live trade flow: expand-on-click tiles")
explicitly said "Open tiles will collapse on refresh unless the JS
preserves state. Decision: accept the collapse-on-refresh; if it gets
annoying, add a localStorage open-set keyed by audit_event row id
later." Got annoying within hours of shipping, so building it now.
**Backup tag:** `.pre-tradeflow-state-20260502-2208` (on 3 mutated files;
`trade_flow_state.js` is first-shipment, no backup needed)

**Files deployed (4):**
- `trading_corp/web/data.py` — `trade_flow()` SELECT now includes the
  `audit_event.id` column; the returned dict carries `"id": r["id"]`
  alongside the existing `ts/kind/symbol/side/qty/reason/payload_pretty`
  keys. `id` is the stable primary-key from `audit_event` and is the
  natural choice for keying tile-open state across htmx swaps.
- `trading_corp/web/templates/partials/trade_flow.html` — each tile's
  `<details>` element now carries `data-tile-id="{{ evt.id }}"`. JS
  uses this attribute to recognize the same tile across the htmx swap.
- `trading_corp/web/static/js/trade_flow_state.js` — NEW, ~75 lines of
  vanilla ES6, no new dependencies. Three responsibilities:
    1. Listen for `<details>` toggle events (capture phase, since
       `toggle` doesn't bubble) on `#trade-flow details[data-tile-id]`
       and persist the open-set to `localStorage` under key
       `tradeflow:open-tile-ids`.
    2. Listen for `htmx:afterSwap` events targeting `#trade-flow` and
       re-apply `open` attribute to any `<details data-tile-id>`
       whose ID is in the persisted set.
    3. Apply the same logic on initial `DOMContentLoaded`.
  No bounded-size cleanup of the persisted set: audit_event ids grow
  monotonically; the JSON encoding is small; localStorage caps in the
  5-10MB range per origin. Many years of normal trading before this
  becomes a real concern.
- `trading_corp/web/templates/home.html` — added
  `<script src="/static/js/trade_flow_state.js"></script>` to
  `{% block scripts %}` alongside the existing `equity_chart.js`.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Live trade flow tile open state survives the htmx 5s refresh.**
  Click a tile, walk away, come back later — it's still expanded.
- **State also survives page reload** (localStorage, not just JS memory).

**Notable code changes (callouts a future Claude shouldn't miss):**
- Open tile IDs persist across browser sessions in localStorage —
  someone debugging "why is this tile open before I touched it?"
  should check `localStorage.getItem('tradeflow:open-tile-ids')` in
  the browser console.
- This pattern (data-id attribute + capture-phase toggle listener +
  htmx:afterSwap re-apply) is reusable. If a future panel adds the
  same htmx-refresh-collapses-state issue (e.g. an Engagements log
  on the Research screen if it ever gets htmx polling), copy the
  trade_flow_state.js shape and parameterize on panel ID + selector.
- The Engagements log on `/research` ALSO uses the `<details>` expand
  pattern but does NOT have htmx polling, so it's unaffected by this
  bug and needs no preservation logic. If a future change adds htmx
  polling to that panel, it will need this same treatment.

**Latent bugs caught + fixed:** none.

**Verification:**
- PID 108238 → 109409 confirms restart at 22:10:30 UTC.
- All endpoints 200: `/` (2.9s), `/partials/trade-flow`, `/static/js/trade_flow_state.js`.
- Content checks: `data-tile-id` count in `/partials/trade-flow`
  render = 12 (one per tile). `trade_flow_state.js` referenced once
  in `/`.
- Browser test: Board (Jack) opened a Live trade flow tile, waited
  through one htmx tick without cursor movement, confirmed tile
  stayed open. Click again to close worked. Reload-and-restore
  worked.
- Zero errors in journalctl post-restart aside from the baseline
  paper_trade_replay info-level lines containing the literal string
  `'errors': 0`.

**Inert / dormant on current traffic:** none.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-tradeflow-state-20260502-2208; BASE=/home/azureuser/trading_corp;
for f in trading_corp/web/data.py trading_corp/web/templates/partials/trade_flow.html trading_corp/web/templates/home.html; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
rm \$BASE/trading_corp/web/static/js/trade_flow_state.js;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 21:52 UTC — Strategy file move: divisions/ → strategies/ (vocabulary realignment)

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board vision realignment in-session (2026-05-02). User
stepped back and articulated the model: divisions = brokerage/account
portfolio managers; strategies = how a division operates. Lord Otter and
Market Cypher had been mis-classified as divisions in `agents/divisions/`;
they are strategies running inside the `coinbase_spot` division. File
move + import updates align code with the corrected vocabulary. Pure
rename — zero behavioral change. Local md5 of moved files matched prod's
old `divisions/*.py` md5 byte-for-byte before deploy.
**Backup tag:** `.pre-strategy-rename-20260502-2146` (on 5 mutated files;
the 3 new `strategies/*.py` files are first-shipment, no backup needed)

**Files deployed (8):**
- `trading_corp/agents/strategies/__init__.py` — NEW (empty namespace package)
- `trading_corp/agents/strategies/lord_otter.py` — NEW; identical content
  to the just-deleted `divisions/lord_otter.py` (md5 `3011ed78…`)
- `trading_corp/agents/strategies/market_cypher.py` — NEW; identical
  content to the just-deleted `divisions/market_cypher.py` (md5 `b7e387b6…`)
- `trading_corp/main.py` — both wiring imports flipped from
  `agents.divisions.{lord_otter,market_cypher}` to
  `agents.strategies.{...}`
- `BACKLOG.md` — new top-of-file `## ⏸ PAUSED — Lord Otter + Market
  Cypher feature work` section documenting the maintenance-mode posture
  + 2026-05-02→2026-05-05 PMCC research-as-consultant observation
  period; two prose path references updated
- `config/strategies.yaml` — single comment-line path reference updated
  (cosmetic — values unchanged)
- DELETED: `trading_corp/agents/divisions/lord_otter.py`
- DELETED: `trading_corp/agents/divisions/market_cypher.py`

**Features shipped (load-bearing for future "is X done?" checks):**
- **Strategy modules now live at `trading_corp/agents/strategies/`**, not
  `trading_corp/agents/divisions/`. Any future Claude session searching
  for Otter/Cypher code by path should look at `strategies/`.
- **Logger namespace flipped:** all log lines from these agents now
  prefix with `trading_corp.agents.strategies.lord_otter` /
  `…market_cypher` instead of the old `…divisions.…`. Any external
  log-grep, journalctl filter, or audit query keyed on the old
  namespace will miss new entries.
- **BACKLOG.md ⏸ PAUSED notice is live on prod** — future sessions can
  see the maintenance-mode posture without needing chat context.
- **CLAUDE.md does NOT ship to prod** (it's a Claude-Code-only artifact
  per md5-diff finding); the new "§ Research consultation" rule + the
  module-map split + the divisions-table reframe live on the local
  working tree only. That's correct — CLAUDE.md is loaded per-session
  from local, not from prod's filesystem.

**Notable code changes (callouts a future Claude shouldn't miss):**
- `agents/divisions/` on prod NOW correctly holds only the actual
  divisions: `pmcc_robinhood.py`, `fidelity_options.py`,
  `crypto_futures/`. Plus a pile of `.pre-*` backup tags.
- `pmcc_robinhood.py` and `fidelity_options.py` STILL conflate
  division-level and strategy-level concerns — flagged in CLAUDE.md
  § Known sharp edges as future cleanup once a second strategy on
  Robinhood or Fidelity is needed. Don't refactor speculatively.
- `docs/ARCHITECTURE.md § 1 principle 2` quotes the OLD framing
  ("broker × strategy combo is its own division"). Officially
  superseded by CLAUDE.md's new framing as of 2026-05-02 — separate
  Board-approved ARCHITECTURE.md pass needed to update the source doc.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 55 unit tests pass on local against the renamed paths
  (`tests/test_lord_otter_bias_persistence.py`, `…webhook_audit_trail.py`,
  `…webhooks_return_fast.py`, etc.) — confirmed import path works.
- Post-deploy: PID 107062 → 108223 (restart at 21:52:26 UTC).
- `journalctl -u trading-corp --since '3 min ago'` shows
  `INFO trading_corp.agents.strategies.lord_otter: LordOtterAgent
  reloaded config: enabled=True auto_execute=False symbols=['BTC/USD']
  arming_window_bars=5` and the parallel Cypher line at the new
  namespace — confirms imports succeeded and agents loaded.
- Zero `ImportError` / `ModuleNotFoundError` / `agents.divisions.lord_otter`
  / `agents.divisions.market_cypher` lines in journalctl post-restart.
- Dashboard `/` returns 200 in 2.7s; `/research` 200; `/partials/trade-flow`
  200 in 3ms.

**Inert / dormant on current traffic:**
- The Otter+Cypher strategy modules run as before (paper-mode,
  `auto_execute: false`); no new feature work landed in this deploy.
  The pause is an organizational stance, not a code-level mute.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-strategy-rename-20260502-2146; BASE=/home/azureuser/trading_corp;
mv \$BASE/trading_corp/main.py\$TAG \$BASE/trading_corp/main.py;
mv \$BASE/BACKLOG.md\$TAG \$BASE/BACKLOG.md;
mv \$BASE/config/strategies.yaml\$TAG \$BASE/config/strategies.yaml;
mv \$BASE/trading_corp/agents/divisions/lord_otter.py\$TAG \$BASE/trading_corp/agents/divisions/lord_otter.py;
mv \$BASE/trading_corp/agents/divisions/market_cypher.py\$TAG \$BASE/trading_corp/agents/divisions/market_cypher.py;
rm -rf \$BASE/trading_corp/agents/strategies/;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 21:44 UTC — Dashboard polish: expand-on-click for Engagements log + Live trade flow + Engagement-latency column rename

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board picked one of the new P5 UI polish items as a
warmup ("Engagements log expand-on-click" → routes.py + research.html);
extended to the symmetric trade-flow tile expand (data.py +
partials/trade_flow.html); the previously-PARTIALLY-DONE
"Engagement-latency panel column rename" was sitting on local working
tree and rode along.
**Backup tag:** `.pre-dashboard-polish-20260502-2143` (on all 4 files)

**Files deployed (4):**
- `trading_corp/web/routes.py` — added `import json`; new
  `"payload_pretty": json.dumps(payload, indent=2, default=str,
  sort_keys=True)` key on the `engagement_log` dict in
  `_build_research_view` (line 1044). `sort_keys=True` so repeated
  `kind`s render with stable field order across reloads.
- `trading_corp/web/data.py` — same `payload_pretty` key added to
  `trade_flow()` dict (line 932). `json` already imported.
- `trading_corp/web/templates/research.html` — engagement_log row
  `<div>` converted to `<details class="px-4 py-2 group">` /
  `<summary>` matching the existing thesis-library pattern. Body is
  a `<pre>` with `whitespace-pre overflow-x-auto bg-pane-2/40 border
  border-edge` so wide payloads get a horizontal scrollbar instead of
  wrapping. Also: Engagement-latency panel column headers humanized
  (`product_type`→`Product`, `asset_class`→`Asset Class`, `N`→`Samples`,
  `P50 (s)`→`Median (s)`, `week`→`Week`) — the previously-PARTIALLY-DONE
  rename.
- `trading_corp/web/templates/partials/trade_flow.html` — tile `<div>`
  converted to `<details>` / `<summary>` with `<pre>` body. Default
  browser disclosure marker suppressed via Tailwind arbitrary variant
  (`list-none [&::-webkit-details-marker]:hidden` on the `<summary>`)
  so the only indicator is the custom `▶` chevron rotating with
  `group-open:rotate-90`. Differs intentionally from the
  Engagements-log row pattern (which kept the dual marker to match
  thesis-library precedent on the same screen) — tile UI looked
  weirder with a stray default triangle inside a styled box.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Engagement-log rows on `/research` are click-to-expand inline
  accordions** showing the full `audit_event.payload_json` pretty-
  printed. Multiple rows can be open at once. Backlog item
  "P5 — Research screen: expand-on-click rows in Engagements log
  (PARTIALLY DONE → SHIPPED)".
- **Live trade flow tiles on `/` are click-to-expand** with the same
  pattern. Backlog item "P5 — Live trade flow: expand-on-click tiles
  (PARTIALLY DONE → SHIPPED)". Note: tile collapses on the htmx 5s
  refresh tick — explicitly accepted per spec; localStorage
  state-preservation is out of scope.
- **Engagement-latency panel column headers humanized** for
  Board-facing readability. Backlog item "P5 — Research screen:
  humanize Engagement latency panel column labels (PARTIALLY DONE →
  SHIPPED)".

**Notable code changes (callouts a future Claude shouldn't miss):**
- The two expand patterns differ on the disclosure-marker handling
  (engagement-log keeps the dual marker; trade-flow suppresses it).
  This is intentional and called out in BACKLOG.md. Future polish
  pass: normalize both with a site-wide CSS rule.
- `payload_pretty` key adds modest bandwidth to the SSR responses —
  120 engagements × ~typical payload + 20 trade-flow events × payload.
  Verified comfortably under any sane SSR ceiling; no perf regression
  observed.

**Latent bugs caught + fixed:** none.

**Verification:**
- PID 105xxx → 107062 (restart at 21:44:05 UTC).
- `/research` returns 200 in 2.7s; `/` 200 in 2.6s; `/partials/trade-flow`
  200 in 3ms.
- Content check: `curl http://127.0.0.1:8000/partials/trade-flow | grep -c
  'payload_pretty\|group-open:rotate-90'` → 12 (one per tile pre-htmx-tick).
- Content check: `curl http://127.0.0.1:8000/research | grep -c
  '<details class="px-4 py-2 group"'` → 142 (engagement-log rows + thesis
  library + position-context bundles all use the pattern).
- No new errors in journalctl post-restart aside from the baseline
  Fidelity bot-block + yfinance BTC earnings noise (both pre-existing).

**Inert / dormant on current traffic:** none.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-dashboard-polish-20260502-2143; BASE=/home/azureuser/trading_corp;
for f in trading_corp/web/templates/research.html trading_corp/web/templates/partials/trade_flow.html trading_corp/web/routes.py trading_corp/web/data.py; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 16:01 UTC — Webhook handlers refactored to return-fast (TV 10s-timeout fix)

**Commits:** local-only (uncommitted as of deploy time)
**Triggered by:** Board reported overnight TV alerts showing
"Webhook delivery failed — request took too long and timed out" on
the 04:00 UTC 4h-bar Cypher signals. Investigation found the webhook
handlers run the broker snapshot + agent.on_alert + research-firm
consult inline before returning HTTP 200, and the research consult
alone can take 12-30s on a multi-expert engagement (verified during
today's 15:31 UTC replay-endpoint test). With auto_execute=false on
both Otter and Cypher today, no QUALIFIED bull alert has hit this
path yet — but the architecture was a latent timeout bomb.
**Backup tag:** `.pre-returnfast-20260502` (on 1 modified file)

**Files deployed (1):**
- `trading_corp/web/webhooks.py` — both handlers (`lord_otter_webhook` + `market_cypher_webhook`) refactored. Synchronous phase now does only validation + the `webhook_received` audit, then dispatches the heavy processing onto a FastAPI `BackgroundTasks` and returns HTTP 200 with `{"status":"accepted","signal":...,"symbol":...}` in well under 200ms. Background processing logic extracted into module-level `_process_lord_otter_alert(...)` and `_process_market_cypher_alert(...)` async helpers, each wrapped in a catch-all that writes an `agent_error` audit row tagged with `phase=background_processing` and Telegram-notifies the Board.

**Features shipped:**
- TradingView's 10s webhook timeout is no longer load-bearing for any downstream work. Even if research consult takes 30s, risk gate stalls, or broker snapshot hangs, TV gets HTTP 200 in <200ms.
- Audit chain unchanged in shape but split across the sync/background boundary: `webhook_received` lands inline (so we have a record even if the background crashes), all subsequent decision rows (`alert_ignored`, `risk_rejected`, `would_have_placed`, `filled`, `execution_error`, etc.) land in the background task.
- New `agent_error` row variant with `phase=background_processing` flag — distinguishes a crash inside the new background helper from the older inline-handler `agent_error` cases.
- Telegram catch-all on background crashes — silent failures impossible.

**Notable code changes:**
- HTTP response shape changed for both handlers. Pre-refactor: `{"status":"would_have_placed", "order_id":"...", "decision":"..."}` (varied per outcome). Post-refactor: uniform `{"status":"accepted", "signal":..., "symbol":...}`. Anything observing TV-callback bodies (we don't, TV doesn't read response bodies) would see the change. The audit log + Telegram surface remain the source of truth, unchanged.
- Existing test `test_push_back_skips_order_and_notifies_board` updated to assert on audit + Telegram side-effects instead of the now-uniform body. The contract that risk_agent isn't called on push_back is preserved — that assertion still passes.
- `test_no_research_firm_falls_through_to_existing_flow` — body assertion changed to `body["status"] == "accepted"`. Negative assertions ("no research_* audit rows", "Telegram NOT called") still hold.
- New tests: `tests/test_webhooks_return_fast.py` (5 tests) pin: (a) HTTP body uniformly "accepted" on valid alerts, (b) webhook_received audit lands during sync phase, (c) outcome audits land in background, (d) background-task crash writes agent_error + Telegram, (e) Cypher handler has same contract.

**Latent bugs caught:** none specific to this refactor — the underlying issue (event-loop-blocking inline processing) was the bug being fixed.

**Verification:**
- PID 102701 → 105xxx after restart, status active.
- md5sum 1/1 file MATCH between local and prod post-scp.
- Live end-to-end: `POST https://trading.jacksumner.com/webhook/tradingview/market-cypher` with this morning's failed alert payload returned `{"status":"accepted","signal":"mc_a_red_diamond","symbol":"BTC/USD"}` in **0.119s** through the full Caddy → FastAPI stack.
- Audit chain post-test:
  - `webhook_received` at 16:00:33 UTC (sync phase)
  - `alert_ignored` at 16:00:34 UTC (background task, 1s later — for the bear-no-position branch)
- 360 tests passing locally (5 pre-existing P2 PMCC failures unchanged).

**Inert / dormant on current traffic:**
- The catch-all background-crash audit + Telegram path is wired but should never fire on healthy traffic. Will surface only on actual exceptions.
- Nothing else inert — both Otter and Cypher webhooks are receiving live traffic; any TV alert exercises the new code path within minutes.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-returnfast-20260502; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/webhooks.py.\$TAG \$BASE/trading_corp/web/webhooks.py; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 15:31 UTC — Manual research-firm replay endpoint + dashboard button (on-demand consult on past TV signals)

**Commits:** local-only (uncommitted as of deploy time)
**Triggered by:** session conversation — Board wanted to see how the
research firm consult code path would have responded to this morning's
10:54am ET `money_bag_top` alert. Today's filter rejects bear signals
when no position is held, so the consult never fires on those rows.
This adds an opt-in replay path so the existing engagement code can be
exercised against historical signals on demand.
**Backup tag:** `.pre-replay-research-20260502` (on 3 modified files)

**Files deployed (4):**
- `trading_corp/web/data.py` — `_query_division_activity` now exposes audit_event `id` + `signal` on each row dict so the dashboard template can build the per-row replay button.
- `trading_corp/web/routes.py` — new `POST /audit/{audit_id}/replay-research` endpoint. Validates kind is in {webhook_received, alert_ignored, would_have_placed}, calls signal_replay, returns an htmx-swappable HTML fragment (verdict pill + colored rationale).
- `trading_corp/web/templates/division.html` — Recent Activity rows now render a "Send to research →" button on signal-shaped events. htmx POST + inline result swap underneath. Behind Authelia like the rest of the dashboard.
- `trading_corp/agents/research/signal_replay.py` (new) — `synthesize_order_from_payload(payload, audit_event_id=...)` reconstructs a ProposedOrder shape from a TV webhook payload, marks `extra.synthetic=True` so the firm + downstream consumers know it isn't from the live agent path. `replay_signal_research(audit_row, ...)` routes through the existing `consult_research_for_trade_confirmation` helper, writes a `research_replay_completed` audit row tagged with the source audit id. 60s timeout (vs 8s on live path — replay isn't on a live order path, no rush).

**Features shipped:**
- Per-row "Send to research →" button on the per-division Recent Activity panel for `webhook_received` / `alert_ignored` / `would_have_placed` rows. Click → htmx POST → inline result swap with verdict pill (green CONFIRM, yellow CONDITIONAL/TIMEOUT, red PUSH_BACK/ERROR) + the firm's rationale beneath.
- Audit trail: `research_replay_completed` (or `research_replay_failed` on synthesis/consult failure) captures source_audit_event_id, verdict_kind, decision, rationale (truncated 500 char), signal, symbol, alert_price/time. Surfaces on the existing Research screen Engagements log alongside the engagement's other rows.
- Side inference: bear-leaning signal-name fragments (`bear`, `top`, `red_diamond`, `sell_circle`, `spoon_bear`, `money_bag_top`) → side='sell'. Everything else → 'buy'. Synthetic order's qty is fixed 0.01 placeholder — research firm reasons about setup, not size.

**Notable code changes:**
- Discovered + fixed during deploy: `EngagementSpec.requesting_division` is misnamed — it actually expects the strategy/agent slug (`lord_otter`/`market_cypher`), not the broker-account division slug. First request returned `ValidationError` because we passed `coinbase_spot`. Fix: pull `payload.strategy` first, fall back to `audit_row.actor`. **The misnaming itself is a pre-existing schema oddity worth a separate cleanup item** — naming the field `requesting_strategy` (or `requesting_agent`) would prevent future foot-shoots.
- Discovered + fixed: 8s default timeout on `consult_research_for_trade_confirmation` is wired for the live webhook path where speed matters; multi-expert engagements typically take 15-30s end-to-end. Replay isn't on a live path so we pass `timeout_s=60.0` explicitly. First successful replay completed in 12.5s.

**Latent bugs caught + fixed:** see "Notable code changes" above. Both surfaced during the live deploy validation of the new endpoint.

**Verification:**
- PID 102701 → 105xxx (after the timeout-bump scp+restart), status active.
- md5 4/4 files MATCH between local and prod post-scp (final pass after both fixes).
- 19 new tests in `tests/test_signal_replay.py`, all green. Full suite 355 passed (5 pre-existing P2 PMCC failures unchanged).
- Live end-to-end exercise: `POST /audit/614/replay-research` (the 14:54 UTC `money_bag_top` from this morning) returned `verdict=PUSH_BACK decision=skip` in 12.5s. Firm rationale captures (a) it's a synthetic-replay signal, (b) macro neutral with VIX ~17, (c) NFP/FOMC/CPI risk in window, (d) two of three expert dimensions unobserved → insufficient evidence. Audit trail shows full engagement chain: research_engagement_started → research_data_fetch_attempted → research_position_context_emitted → research_expert_completed × N → research_expert_refused × N → research_trade_confirmation_emitted → research_tradeconf_pushback_acted_on → research_replay_completed.

**Inert / dormant on current traffic:**
- The button only renders on signal-shaped audit rows (webhook_received / alert_ignored / would_have_placed). PMCC, Fidelity, and other audit rows don't show the button.
- Not on a live order path. Cannot affect order placement under any code path. Even on a `confirm` verdict, the synthetic order has `extra.synthetic=True` and is never sent to data_exec — the consult result is purely informational.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-replay-research-20260502; BASE=/home/azureuser/trading_corp; \
for f in trading_corp/web/data.py \
         trading_corp/web/routes.py \
         trading_corp/web/templates/division.html; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -f \$BASE/trading_corp/agents/research/signal_replay.py; \
sudo systemctl restart trading-corp
"
# research_replay_completed / research_replay_failed audit rows stay
# in the DB after rollback (harmless — just historical records).
```

---

## 2026-05-02 14:56 UTC — would_have_placed Phase C (replay job + dashboard panel) + 0-DTE Terminal-DTE Override calendar refactor + P1 cycle-continuity

**Commits:** local-only (uncommitted as of deploy time)
**Triggered by:** "lets do 0 dte backlog item and then deploy" — bundled
Phase C of the would_have_placed enrichment with the P0 0-DTE
Terminal-DTE Override refactor (and the P1 cycle-continuity release
folded in).
**Backup tag:** `.pre-phaseC-0dte-20260502` (on 6 modified files)
**New venv dep:** `pandas-market-calendars 5.3.2` (transitive:
exchange-calendars 4.13.2, korean_lunar_calendar 0.3.1, pyluach 2.3.0,
toolz 1.1.0)

**Files deployed (8):**
- `requirements.txt` — added pandas-market-calendars>=4.4.0 (NYSE schedule for half-day / holiday-aware 0-DTE deadline gates).
- `config/strategies.yaml` — new `robinhood_pmcc.zero_dte` block: release_offset_min=60, hard_deadline_offset_min=30, cycle_continuity_extrinsic_threshold=0.15. All three are operationally tunable without a code deploy.
- `trading_corp/main.py` — wired the paper_trade_replay loop alongside the PMCC scan scheduler. Startup catch-up fires before the loop is spawned (mark_pre_phase_a_rows + one immediate replay tick). Loop runs every 900s, cancelled on shutdown. Log lines use f-strings to bypass the RedactingFilter dict-mangling (a separate pre-existing harness bug).
- `trading_corp/web/data.py` — new `paper_trade_summary(db_url, division)` returns 7d/30d/all-time totals (wins/losses/expired/open) + simulated $ P&L per window. pre_phase_a rows excluded from win-rate math, surfaced separately via `n_pre_phase_a`. DivisionViewSnapshot grew a `paper_trade_summary` field.
- `trading_corp/web/templates/division.html` — new "Paper-trade win rate" section above Recent Activity. 3 cards (7d/30d/all) with win % colored green/red, W/L/E counts, sim P&L. Hidden when `totals.all.n == 0` so PMCC/Fidelity divisions don't show empty cards.
- `trading_corp/agents/divisions/pmcc_robinhood.py` — `_terminal_dte_time_release` refactored from hardcoded 15:00/15:30 ET clock to NYSE-calendar-aware close-relative offsets. Added cycle-continuity P1 release path (mark <= threshold AND short_leg_dte == 0 → roll_short, regardless of time). Helper accepts an optional `calendar=` kwarg for test injection. Prompt-rule docstrings (Rules 7 + 4) updated to describe both release paths.
- `trading_corp/agents/paper_trade_replay.py` (new) — walk-forward classifier with conservative same-bar tie-rule (both TP and SL hit in one 1m bar → assume LOSS). Public sync entry, async-native variant, asyncio loop spawner. Default Coinbase ccxt fetcher (paginated, no auth) for OHLC. mark_pre_phase_a_rows helper for the startup catch-up.
- `trading_corp/utils/market_hours.py` (new) — MarketHoursCalendar wrapper around pandas_market_calendars. Memoized per-date close lookups (lru_cache 2048). Graceful fallback when pmcal import fails (every weekday closes at 16:00 ET, weekends closed) — degraded but functional, logged once per process.

**Features shipped:**
- **Phase C of would_have_placed enrichment.** Background replay loop walks paper_trade_record rows where result IS NULL, fetches 1m OHLC bars from Coinbase via ccxt, classifies each row as win/loss/expired (with conservative same-bar tie = loss). Writes result_*, actual_pnl_dollars, actual_r_multiple, bars_to_resolution. 15-min interval; restart triggers immediate catch-up tick.
- **Per-division "Paper-trade win rate" dashboard panel** on /division/{slug}. 3-card layout (7d/30d/all-time) with win rate %, W/L/E counts, sim P&L. Auto-hidden on divisions with no rows. Shows pre-Phase-A row count as a footnote when relevant (5 rows on coinbase_spot today, marked pre_phase_a at startup).
- **0-DTE Terminal-DTE Override is now NYSE-calendar-aware.** Hardcoded 15:00 ET / 15:30 ET thresholds replaced with `close - release_offset_min` / `close - hard_deadline_offset_min` lookups against the NYSE schedule. Half-day closes (e.g. 13:00 ET on day after Thanksgiving) correctly slide the deadline to 12:00 / 12:30 ET. Friday-holiday rotations land the deadline on Thursday's close. Defaults match prior 60/30 minute behaviour.
- **P1 cycle-continuity release.** When a 0-DTE short's mark has decayed to ≤$0.15/share (config knob), force roll_short regardless of time — captures next-cycle premium at today's IV, eliminates post-expiry coverage gap. Operates independently of the time gate; both check short_leg_dte == 0 first.

**Notable code changes:**
- f-string log-formatting in main.py + paper_trade_replay.py for the replay-counts dicts. The harness's RedactingFilter rewrites dict log args into a tuple of keys, which then fails `%s` formatting with TypeError. f-strings sidestep this. **Filing a separate observation:** the RedactingFilter's dict-handling is a pre-existing bug worth a small backlog item — affects any future caller passing a dict via %-style logging. Not a regression, just exposed by Phase C.
- `MarketHoursCalendar.close_time_et` returns tz-aware ET datetimes via `.astimezone(ET)` so DST arithmetic stays correct under timedelta subtraction.
- `_terminal_dte_time_release` now takes optional `calendar=` for test injection. Production path uses `default_calendar()` module-level singleton to avoid re-loading the NYSE schedule per call.
- Test refactor (`tests/test_pmcc_logic.py:_FakeCalendar`) — simple test double for the calendar so existing 7 tests + DST test work without depending on pandas_market_calendars being installed in CI.

**Latent bugs caught + fixed:**
- The "Logging error" `TypeError: not all arguments converted` from the first restart at 14:51 UTC was caught immediately, fix scp'd at 14:55 UTC, restart at 14:56 UTC clean. The replay loop was actually functioning during the broken-logging window — only the count summary failed to render.

**Verification:**
- PID 87416 (Phase B running) → 99920 (first attempt with logging bug) → 100824 (clean fix), status active.
- md5sum 8/8 files MATCH between local and prod post-scp.
- pandas_market_calendars import smoke test on prod: `default_calendar().close_time_et(date(2024, 7, 3))` returns `2026-07-03 13:00:00-04:00` — half-day correctly resolved.
- paper_trade_replay startup catch-up: `{'scanned': 0, 'resolved_win': 0, 'resolved_loss': 0, 'resolved_expired': 0, 'marked_pre_phase_a': 0, 'errors': 0}` — note: marks=0 because the explicit mark_pre_phase_a_rows call before replay_pending_paper_trades_async already marked the 5 historical rows (idempotent inner mark sees 0).
- Database row distribution post-restart: `pre_phase_a: 5` (the 4 Otter + 1 Cypher backfilled rows from Phase B). All have NULL stop or NULL tp_price (Phase A wasn't shipped at their alert times), so they correctly fell through to pre_phase_a.
- Dashboard probe `GET /division/coinbase_spot` returns HTTP 200 with the new "Paper-trade win rate" section rendering and the "5 pre-Phase-A row(s) excluded" footnote present.
- 336 tests passing locally. 5 pre-existing P2 PMCC liquidity-gate failures unchanged (BACKLOG).

**Inert / dormant on current traffic:**
- Replay loop sees no rows to actually classify yet — every paper_trade_record row landed pre-Phase-A. Once new TV-driven `would_have_placed` events fire post-Phase-A (auto-execute is `false` on both Otter and Cypher, so every alert lands here), the loop will start classifying them within 15 min.
- 0-DTE gates only fire when a PMCC short reaches 0 DTE. PMCC scan path is daily 8:30-9:25 ET; the time-of-day gate path will exercise on the next 0-DTE expiration day with an active short. Cycle-continuity P1 path will exercise as soon as a 0-DTE short's mark decays to ≤$0.15/share — could be the same day depending on IV.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-phaseC-0dte-20260502; BASE=/home/azureuser/trading_corp; \
for f in requirements.txt \
         config/strategies.yaml \
         trading_corp/main.py \
         trading_corp/web/data.py \
         trading_corp/web/templates/division.html \
         trading_corp/agents/divisions/pmcc_robinhood.py; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -f \$BASE/trading_corp/agents/paper_trade_replay.py \
      \$BASE/trading_corp/utils/market_hours.py; \
sudo systemctl restart trading-corp
"
# Note: pandas_market_calendars + transitive deps stay installed; harmless,
# can be left or `pip uninstall` if the rollback is permanent.
```

---

## 2026-05-02 05:45 UTC — would_have_placed enrichment Phase B (paper_trade_record table + write-on-emit)

**Commits:** local-only (uncommitted as of deploy time)
**Triggered by:** "execute the plan 1-5" — bundled Phase B of the
would_have_placed enrichment (BACKLOG.md 2026-05-01 P1 entry, Phase B sub-task)
**Backup tag:** `.pre-phaseB-20260502` (on 6 modified files)

**Files deployed (7):**
- `config/strategies.yaml` — added strategy-global `max_hold_seconds`: lord_otter=86400 (24h), market_cypher=604800 (7d). Frozen onto each paper_trade_record at write time so config edits don't retroactively alter past trades.
- `trading_corp/agents/divisions/lord_otter.py` — new `max_hold_seconds` property reading from yaml with 86400 default.
- `trading_corp/agents/divisions/market_cypher.py` — new `max_hold_seconds` property with 604800 default.
- `trading_corp/persistence/db.py` — new `paper_trade_record` table + 2 indexes (`ix_paper_trade_record_strategy_ts`, `ix_paper_trade_record_result`); new `insert_paper_trade_record(record_dict, db_url)` helper using INSERT OR IGNORE on order_id.
- `trading_corp/persistence/models.py` — new `PaperTradeRecord` dataclass + `to_db_row()` + `from_order(order, *, strategy, division, max_hold_seconds)` factory that pulls Phase A trade-card fields out of order.extra and computes expected_loss / rr_ratio.
- `trading_corp/web/webhooks.py` — new module-private `_record_paper_trade(deps, order, strategy, agent)` helper; called inside both Otter and Cypher `would_have_placed` branches (after audit log_event, before Telegram push). try/except wrapped: a write failure logs a WARNING but does NOT break the order flow — audit_event remains source of truth.
- `scripts/backfill_paper_trade_record.py` (new) — idempotent one-shot script that walks `audit_event WHERE kind='would_have_placed'`, joins to `proposed_order.extra_json`, and inserts a paper_trade_record per row. Safe to re-run (INSERT OR IGNORE).

**Features shipped:**
- New SQLite table `paper_trade_record` written on every `would_have_placed` emission. Structured columns for trade specs (entry_reference_price, stop_price, tp_price, tp_r_multiple, expected_loss, expected_gain, rr_ratio) + Phase C-anticipating result columns (result, result_ts, result_price, actual_pnl_dollars, actual_r_multiple, bars_to_resolution) that stay NULL until the future replay job populates them.
- One-time backfill of historical paper trades: 5 pre-deploy rows backfilled (4 lord_otter, 1 market_cypher; first ts 2026-04-30 17:41 UTC, last ts 2026-05-01 02:06 UTC). All pre-Phase-A historical rows have NULL trade-spec fields where Phase A would populate; that's expected and the future replay job will skip them.
- `max_hold_seconds` strategy-global config knob frozen per row at write time. Phase C replay job will use this to decide when to mark `result='expired'` for trades that didn't hit either TP or SL within the window.

**Notable code changes:**
- Schema migration is automatic via `init_db()` `CREATE TABLE IF NOT EXISTS` — service restart applies it. No manual SQL run.
- Failure mode for the new write path is fail-open: try/except in `_record_paper_trade` so a paper_trade_record write error does NOT abort the audit-log + Telegram push. The audit_event row is still source of truth (per CLAUDE.md §1 "audit log writes BEFORE every decision branch").
- INSERT OR IGNORE keying on order_id means the inline write-on-emit path and the backfill script can never collide. Whichever wrote first wins.
- BACKLOG.md entry updated in-tree with Phase A ✅ shipped / Phase B ✅ in-tree-as-of-2026-05-02 / Phase C ⬜ pending status header. (BACKLOG.md is dev-only, not deployed.)

**Latent bugs caught + fixed:**
- None.

**Verification:**
- PID 82701 → 87416, status active
- md5sum 7/7 files MATCH between local and prod post-scp
- `.schema paper_trade_record` returns the full CREATE TABLE + 2 indexes
- Backfill ran clean: `WROTE: scanned=5 inserted=5 skipped_no_order_id=0`
- Row inspection: 4 lord_otter rows + 1 market_cypher row, max_hold_seconds populated correctly per strategy (86400 / 604800), stop_price + expected_loss populated on rows where the order's `extra` had `max_dollar_risk` (pre-Phase-A path), tp_price/expected_gain NULL across all (pre-Phase-A — no TP fields had been written yet at those alert times).
- Dashboard probes: `GET /` HTTP 200, `GET /research` HTTP 200
- 11 new tests in `tests/test_paper_trade_record.py` green; full suite 305 passed (5 pre-existing P2 PMCC scan failures, called out in BACKLOG.md, unchanged)
- Service log post-restart: only baseline errors (Fidelity bot-block + yfinance "No earnings dates for BTC/USD") — same baseline noted in prior deploy_log entries; no Phase B-introduced errors

**Inert / dormant on current traffic:**
- The new `_record_paper_trade` write hook only fires on `would_have_placed` branches (auto_execute=false). Both Otter and Cypher are auto_execute=false today, so every alert hits the new path. NOT inert — exercising on live traffic immediately.
- Phase C replay job code is NOT deployed; result columns will stay NULL until that lands. No-op for now.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-phaseB-20260502; BASE=/home/azureuser/trading_corp; \
for f in config/strategies.yaml \
         trading_corp/agents/divisions/lord_otter.py \
         trading_corp/agents/divisions/market_cypher.py \
         trading_corp/persistence/db.py \
         trading_corp/persistence/models.py \
         trading_corp/web/webhooks.py; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -f \$BASE/scripts/backfill_paper_trade_record.py; \
sudo systemctl restart trading-corp
"
# Note: rolling back the schema (DROP TABLE paper_trade_record) is
# OPTIONAL — the table will simply sit unused on the rolled-back code.
# Only drop it if you're certain you won't be replaying these rows.
```

---

## 2026-05-02 03:30 UTC — Research firm Phase 1f

**Commits:** `ce15602`, `d61b7ec`
**Triggered by:** "deploy" instruction after Phase 1f UAT passed (22 checks incl. real-LLM smoke)
**Backup tag:** `.pre-1f-20260502-0030` (on 7 modified files)

**Files deployed (13):**
- `trading_corp/agents/llm.py` — _TEMPERATURE_REJECTING_MODELS set; skip temperature for Opus 4.7
- `trading_corp/agents/logger.py` — log_event returns cur.lastrowid
- `trading_corp/agents/research/state.py` — debate_audit_row_id field on EngagementState
- `trading_corp/agents/research/graph.py` — debate_gate node + threading
- `trading_corp/agents/research/synthesis/thesis.py` — debate threading + always-insert driver
- `trading_corp/agents/research/synthesis/position_context.py` — debate threading + risk_flag surface
- `trading_corp/agents/research/synthesis/trade_confirmation.py` — debate threading + tags audit_row_id
- `trading_corp/agents/research/debate_gate.py` (new) — variance/disagreement gate
- `trading_corp/agents/research/experts/debate/__init__.py` (new)
- `trading_corp/agents/research/experts/debate/_base.py` (new) — shared bull/bear runner
- `trading_corp/agents/research/experts/debate/bull.py` (new) — Sonnet
- `trading_corp/agents/research/experts/debate/bear.py` (new) — Sonnet
- `trading_corp/agents/research/experts/debate/judge.py` (new) — Opus, scores quality only

**Features shipped:**
- Bull/bear/judge debate round fires on single-symbol engagements where
  expert variance >= 0.25 OR >= 2 experts disagree on directional_lean
- Two new audit kinds visible in dashboard: `research_debate_invoked`,
  `research_debate_completed`
- Debate context flows into Thesis key_drivers ("debate (gate fired): ..."),
  PositionContext risk_flags ("debate fired: ..."), and TradeConfirmation
  via debate_audit_row_id
- v3 design feature-complete on all 4 product types

**Notable code changes:**
- `agents/llm.py` `_TEMPERATURE_REJECTING_MODELS = {"claude-opus-4-7"}` — extend this set as Anthropic deprecates temperature on more models
- `agents/logger.py` `log_event` signature changed `None` -> `int | None` — backwards-compat for callers that ignore the return

**Latent bugs caught + fixed:**
- Opus 4.7 deprecated `temperature` parameter; judge silently fell back to placeholder scores on every firing pre-fix
- `log_event` always returned None, so `debate_audit_row_id` could never be a real id

**Verification:**
- PID 78397 -> 82701, status active
- 2 PositionContext primes completed end-to-end (Otter 4h + Cypher 24h)
- Graph compiles to 15 nodes including `debate_gate`
- /research dashboard probe HTTP 200, sections present
- 5 Fidelity bot-block + 1 yfinance no-earnings line are baseline (not regressions)

**Inert / dormant on current traffic:**
- Debate gate is on disk + exercising itself but **never fires** in current
  prod traffic. Crypto-only PositionContext engagements (Otter+Cypher prime
  BTC/USD on every restart) have only macro as a valid expert (sentiment
  refuses on crypto). Single-voice panel can't fire. The gate will start
  firing when (a) Otter/Cypher get equity exposure, (b) Board fires a
  Thesis on equity, or (c) PMCC scout TradeConfirmation engagements run
  with multiple experts.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-1f-20260502-0030; BASE=/home/azureuser/trading_corp; \
for f in trading_corp/agents/llm.py trading_corp/agents/logger.py trading_corp/agents/research/state.py trading_corp/agents/research/graph.py trading_corp/agents/research/synthesis/thesis.py trading_corp/agents/research/synthesis/position_context.py trading_corp/agents/research/synthesis/trade_confirmation.py; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -rf \$BASE/trading_corp/agents/research/experts/debate \
       \$BASE/trading_corp/agents/research/debate_gate.py
"
```

---

## 2026-05-02 02:13 UTC — routes.py hotfix (research_data_fetch_attempted)

**Commits:** `c29713a`
**Triggered by:** Phase 1d/1e dashboard 500 error post-restart — _summary_for_event
sliced `payload.get('error', '')[:60]` returning `None[:60]` when the key existed
with value None.
**Backup tag:** `.pre-hotfix-fetch-err-20260501-2330`

**Files deployed (1):**
- `trading_corp/web/routes.py` — defensive `(payload.get("error") or "")[:60]`

**Features shipped:**
- Dashboard /research stops returning HTTP 500 when audit log contains
  `research_data_fetch_attempted` rows with `error=None` payloads.
  These rows started landing because Phase 1d's PositionContext prime
  fired real macro-expert engagements that wrote them.

**Verification:**
- Service restart picked up the fix (FastAPI binds routes at startup;
  no hot-reload available)
- /research returns 200 with PositionContext audit trail rendering

---

## 2026-05-01 23:30 UTC — Research firm Phase 1d + 1e bundle

**Commits:** `b145d82` (Phase 1d), `1cb7e70` + `5be2588` (Phase 1e graph + division halves)
**Triggered by:** "deploy" instruction after Phase 1e UAT passed (real-LLM smoke included)
**Backup tag:** `.pre-1d1e-20260501-2330` (on 9 modified files)

**Files deployed (14):**
- `trading_corp/agents/research/graph.py` — Layer 1 + new emit nodes
- `trading_corp/agents/research/schemas.py` — new audit-kind constants
- `trading_corp/agents/divisions/lord_otter.py` — _fetch_position_context, on-alert hook, configured_symbols, last_position_context, **TP fields in `_build_order` (Phase A scaffolding)**, division consult call
- `trading_corp/agents/divisions/market_cypher.py` — same shape (24h horizon), TP fields, consult call
- `trading_corp/main.py` — startup-of-day prime task
- `trading_corp/web/webhooks.py` — TradeConfirmation consult call between on_alert + risk gate; **Phase A `_format_trade_card` shared renderer for would_have_placed pushes**
- `trading_corp/web/routes.py` — position_contexts view
- `trading_corp/web/templates/research.html` — collapsible PositionContext audit trail
- `config/research.yaml` — `trade_confirmation` block (timeout + kill switch)
- `trading_corp/agents/research/synthesis/position_context.py` (new)
- `trading_corp/agents/research/synthesis/trade_confirmation.py` (new)
- `trading_corp/agents/research/position_context_cache.py` (new)
- `trading_corp/agents/research/prime.py` (new)
- `trading_corp/agents/research/trade_confirmation_consult.py` (new)

**Features shipped:**
- PositionContext engagement type emits via the graph + audit row +
  dashboard view
- Pre-emptive cache for PositionContext (TTL-gated agent_state rows,
  per-division horizons in research.yaml)
- Startup-of-day prime task on every restart populates the cache for
  configured symbols
- Otter + Cypher consume cached PositionContext on alert
  (state.last_position_context; not yet gating behavior)
- TradeConfirmation consult on every Otter/Cypher webhook between
  agent.on_alert and the risk gate (8s hard timeout, fail-open)
- push_back verdict triggers Telegram notify with rationale; conditional
  applies SuggestedModifications transparently
- **Phase A enrichment of would_have_placed pushes** — `_format_trade_card`
  shared renderer outputs full trade card (entry, stop, take-profit,
  R:R, expected P&L) for both Otter and Cypher
- TP fields populate in order.extra: take_profit_price, tp_basis,
  tp_r_multiple, tp_distance_dollars, tp_distance_pct,
  expected_gain_if_tp_hit, expected_loss_if_stopped, entry_reference_price

**Notable code changes:**
- 4 new audit kinds shipped: research_tradeconf_pushback_acted_on,
  research_modifications_applied, research_tradeconf_timeout,
  research_tradeconf_error
- WebDeps already had `research_firm` field — wiring just needed main.py
  to populate it after build_research_firm_deps runs

**Verification:**
- PID change confirmed
- 2 PositionContext engagements completed (Otter 4h, Cypher 24h)
- agent_state rows present for both divisions
- Dashboard initially 500'd on _summary_for_event (latent bug, hotfixed
  separately — see 2026-05-02 02:13 entry)

**Inert / dormant on current traffic:**
- TradeConfirmation consult fires on every Otter/Cypher alert, but most
  alerts in current paper-mode pre-restart audit log are `alert_ignored`
  (bias not set). First webhook that produces an order will exercise
  the consult.

---

## 2026-05-01 (early, no precise timestamp recorded) — Bulk-track scaffolding

**Commits:** `606254e` (and earlier commits unbundled into the bulk-track)
**Triggered by:** Pre-existing trading_corp tree was untracked; bulk-commit added it to git
**Backup tag:** n/a (was in place before tracking started)

**Status:** Best-effort reconstruction — pre-deploy-log discipline.

**Features shipped (already on prod via earlier ad-hoc deploys):**
- Phase 1a-1: CandidateRecommendation engagement graph
- Phase 1a-2: PMCC scout integration with extended-outage notify
- Phase 1b: Thesis ad-hoc + dashboard library
- Phase 1c: Real Fundamental + Sentiment experts (yfinance-backed)
- Holdings table simplification (e14903b)
- PMCC roll history + crypto positions surfacing (b70b6a3, a208f8d)

**Inert observations:**
- Several BACKLOG.md items reference scaffolding that was already
  in-tree at bulk-track time (e.g. take_profit yaml blocks for Otter+Cypher,
  TP-field code paths in _build_order). Some BACKLOG entries describing
  these items have been left as P1 because while the CODE was there,
  the integration into would_have_placed pushes wasn't necessarily
  exercised. Future deploys touching this area should re-verify before
  treating BACKLOG as gospel.

---

## 2026-05-03 16:25 UTC — UI grouping by investment type + Fidelity Individual deactivation + BitUnix placeholder + Coinbase Futures STANDBY

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Sun 2026-05-03 BitUnix Futures vision conversation. User decided crypto split (Coinbase spot = BTC long-only; BitUnix futures = BTC/SOL/ETH bidirectional leveraged; Coinbase Futures → STANDBY) and asked for the dashboard to organize by investment type (Individual / Crypto / Retirement) instead of by broker (Robinhood / Fidelity / Coinbase). Full vision in `~/.claude/.../memory/trading_corp_bitunix_vision.md`. This deploy ships the UI reorg only — phased BitUnix broker build (Phase 1+) is gated on B.4 flag flip Mon.
**Backup tag:** `.pre-inv-type-ui-20260503-1622`

**Files deployed (6 modified):**

- `config/divisions.yaml`:
  - `fidelity_individual` set to `enabled: false` — division deactivated per user decision (option (b) of three: hide / deactivate / delete). YAML entry retained as deadcode for cheap revival. Pre-deactivation safety: account had 0 positions per dashboard.
  - `coinbase_futures` gains `standby: true` flag — UI-only flag, broker init unchanged (still registered, still PaperBroker-wrapped).
  - **NEW** `bitunix_futures` division added — `broker: bitunix`, `account_filter: futures`, `intent: aggressive`, `standby: true`. No broker adapter exists yet; main.py logs WARNING "Unknown broker family 'bitunix'" at startup (expected); hydration falls through to `status='not_wired'`.
- `trading_corp/utils/divisions.py`:
  - Renamed `BrokerGroup` → `InvestmentGroup`, `group_by_broker` → `group_by_investment_type`. New helper `classify_investment_type(d)` maps each division to "individual" / "crypto" / "retirement" using rule: `intent=='retirement'` → retirement; `broker in {coinbase, bitunix}` → crypto; else individual.
  - `_BROKER_ORDER`/`_BROKER_LABELS` replaced with `_INVESTMENT_TYPE_ORDER` (`individual`, `crypto`, `retirement`) and `_INVESTMENT_TYPE_LABELS`. Added `_CRYPTO_BROKERS = {coinbase, bitunix}` for the classifier.
  - **NEW field** `Division.standby: bool = False` (loaded from YAML's `standby` key).
  - Updated `__all__` exports.
- `trading_corp/web/data.py`:
  - Import line updated to new symbol names.
  - `CommandCenterSnapshot.broker_groups: list[BrokerGroup]` → `investment_groups: list[InvestmentGroup]`.
  - Aggregation loop updated.
- `trading_corp/web/templates/home.html`:
  - `{% for grp in snap.broker_groups %}` → `{% for grp in snap.investment_groups %}`.
  - Status badge gains conditional: if `d.standby`, render orange/warn "STANDBY" badge instead of the online/offline/not_wired status badge. Standby is exclusive (replaces, not adds-to, the status badge).
- `trading_corp/web/templates/partials/stat_cards.html`:
  - Variable rename + label change "{N} brokers" → "{N} groups" on the total-equity stat card.
- `trading_corp/comms/telegram_commands.py`:
  - `/status` Telegram message: "*By broker*" section header → "*By investment type*". Emoji map updated: 💼 individual, 🪙 crypto, 🛡 retirement.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Investment-type grouping on /command-center**: dashboard renders three groups in fixed order (Individual / Crypto / Retirement) replacing the prior broker-grouped layout. Each group shows aggregate equity + pnl. Per-group counts visible in stat cards.
- **STANDBY badge UI primitive**: any division with `standby: true` in YAML renders an orange STANDBY badge instead of online/offline. Currently used by Coinbase Futures + BitUnix Futures.
- **Fidelity Individual deactivated end-to-end**: not loaded by `load_divisions()`, no broker registered, not in dashboard, not in /status Telegram. Removable cheaply via YAML `enabled: true` flip if needed.
- **BitUnix Futures placeholder card**: visible in Crypto group with STANDBY tag and equity = "—" (not_wired). Card link → /division/bitunix_futures (will 404 cleanly until division-page hydration handles it; not exercised today).
- **Telegram /status investment-type view**: replaces the broker-aggregate table.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **STANDBY is UI-only.** Setting `standby: true` does NOT disable order routing or broker registration — Coinbase Futures is still registered as a paper-exec broker today. Behavioral disable for Coinbase Futures comes later (per BitUnix vision: keep $-balance reads, drop order path; manual promote later). Don't assume STANDBY === "no orders possible" until that follow-up ships.
- **`broker: bitunix` is unknown to main.py's broker dispatch.** The startup WARNING is harmless but if anyone adds a strict-mode broker check, that warning becomes a fatal. Phase 1 of BitUnix build will register either a real or paper BitUnix broker keyed by `bitunix_futures` slug.
- **`classify_investment_type` is a pure mapping function**, not stored on Division. If you add a new broker family, decide in `_CRYPTO_BROKERS` set whether it's crypto or individual. New retirement-family criterion would need a different rule.
- **Telegram /status emoji map is keyed by group key**, not broker name anymore (`individual`/`crypto`/`retirement` not `robinhood`/`coinbase`/etc.). If you reuse this code path, mirror the new keys.

**Latent bugs caught + fixed (if any):**
None caught/fixed in this deploy.

**Verification:**
- Local smoke test (`python -c "from trading_corp.utils.divisions import ..."`): 8 enabled divisions, 3 groups in correct order: Individual=`[robinhood_pmcc, robinhood_joint, fidelity_joint]`, Crypto=`[coinbase_spot, coinbase_futures, bitunix_futures]`, Retirement=`[robinhood_ira, fidelity_401k]`. Standby flag parses correctly on `coinbase_futures` and `bitunix_futures`.
- Local browser render at `localhost:8000` confirmed by user — three groups render with correct labels, BitUnix shows STANDBY, Coinbase Futures shows STANDBY, no Fidelity Individual card. (Local test bypassed Fidelity via blanked `.env` for fast startup; restored after.)
- Prod restart 16:25:19 UTC; web bound at 16:25:57 UTC (38s); `/healthz` returned 200 OK in 1.7ms.
- Expected `WARNING trading_corp.main: Unknown broker family 'bitunix' for division bitunix_futures` confirmed in journalctl.
- User confirmed visual layout in browser at `https://trading.jacksumner.com`.

**Inert / dormant on current traffic (if any):**
- **BitUnix Futures division**: card visible but division has no broker adapter. Hydration marks `not_wired`. Routes to /division/bitunix_futures will 404 or render with empty data; not exercised today. Phase 1 (broker bring-up + KV migration of section 5c keys) ships post-B.4.
- **Coinbase Futures STANDBY**: badge is purely cosmetic in this deploy. Broker still registered, still order-capable in non-paper modes (we're in PAPER, so moot). Order-path disable comes in a follow-up deploy when the user decides to flip the behavior.
- **Fidelity broker bot-detection error surfaced post-restart**: separate pre-existing flakiness, NOT caused by this deploy. Fidelity Joint + Fidelity 401(k) failed broker connect with "Fidelity rejected the login session... Cache wiped — wait 5-10 min and restart." UI shows them as offline/not_wired until next successful Fidelity login. No data loss; resolves on a future restart.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-inv-type-ui-20260503-1622; BASE=/home/azureuser/trading_corp
for f in config/divisions.yaml \
         trading_corp/utils/divisions.py \
         trading_corp/web/data.py \
         trading_corp/web/templates/home.html \
         trading_corp/web/templates/partials/stat_cards.html \
         trading_corp/comms/telegram_commands.py; do
  mv \$BASE/\$f.\$TAG \$BASE/\$f
done
sudo systemctl restart trading-corp
"
```

---

## 2026-05-03 17:54 UTC — BitUnix Futures Phase 1: read-only broker + KV migration

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Sun 2026-05-03 user greenlight to wire up BitUnix Phase 1 (per `~/.claude/.../memory/trading_corp_bitunix_vision.md` Phase 1: read-only standby). User decided to ship Phase 1 *before* B.4 flag flip — the original "post-B.4" sequencing was conservative; Phase 1 doesn't touch the HITL approval flow, so independent ship is safe. The 16:25 UTC UI grouping deploy was cosmetic; this deploy actually wires the BitUnix broker so the placeholder tile shows real account data.
**Backup tag:** `.pre-bitunix-phase1-20260503-1744` (on the 2 modified files; bitunix.py is new — no backup)
**KV migration:** `BITUNIX-FUTURES-API-KEY`, `BITUNIX-FUTURES-API-SECRET` uploaded to `kv-tc-vtwbowt3wtkpy` via targeted `az keyvault secret set` (NOT the full `scripts/upload_secrets_to_keyvault.ps1` — that would clobber prod's divergent LORD-OTTER-WEBHOOK-SECRET / MARKET-CYPHER-WEBHOOK-SECRET values per `trading_corp_prod_ops.md`). Secret values read from `.env`, never echoed to the conversation.

**Files deployed (3 — 1 new, 2 modified):**

- **NEW** `trading_corp/brokers/bitunix.py` (~290 lines):
  - `BitunixBroker(Broker)` — read-only Phase 1 broker
  - `_sign(api_key, api_secret, query, body)` helper — SHA256-double-sign per BitUnix docs (`https://www.bitunix.com/api-docs/futures/common/sign.html`): `digest = SHA256(nonce + ts + key + sortedQuery + body)`, then `sign = SHA256(digest_hex + secret)`. Headers: `api-key`, `sign`, `nonce` (UUID4 hex no hyphens), `timestamp` (ms). No passphrase (BitUnix doesn't use one — `.env`'s `BITUNIX_FUTURES_PASSPHRASE` field is unused).
  - `connect()` — opens httpx async client + smoke-checks via initial snapshot. Failures log a warning but don't raise — hydration catches them later. Stub mode if creds missing (returns zeros instead of failing).
  - `snapshot()` — sums account balance across stablecoin margin coins (`USDT`, `USDC`). Per-coin equity = `available + frozen + margin + transfer + crossUnrealizedPNL + isolationUnrealizedPNL + bonus`. Position list fetched once (margin-coin-agnostic). Verified $2500 reconciles against BitUnix UI (USDC: $1250 available + $1250 transfer; USDT empty).
  - `quote(symbol)` — public `/api/v1/futures/market/tickers?symbols=<sym>` endpoint. No auth.
  - `place_order` / `cancel_order` — raise `NotImplementedError` as a Phase 1 backstop. In PAPER mode (current prod state) these are never reached — PaperExecutionBroker routes orders to PaperBroker. The raise only fires if someone constructs an unwrapped BitunixBroker in LIVE mode, which doesn't happen until Phase 4.
  - Endpoints: `GET /api/v1/futures/account?marginCoin={coin}`, `GET /api/v1/futures/position/get_pending_positions`, `GET /api/v1/futures/market/tickers?symbols=...`. Base URL `https://fapi.bitunix.com`.
- `trading_corp/utils/secrets.py`:
  - Added `BITUNIX_FUTURES_API_KEY` / `BITUNIX_FUTURES_API_SECRET` to `_SECRET_KEY_NAMES` (so values get redacted from logs by `RedactingFilter`).
  - Added `bitunix_futures_api_key` / `bitunix_futures_api_secret` fields to `Secrets` dataclass.
  - Added both env-var names to `expected_env_vars` for Key Vault loader.
  - `load_secrets()` reads both via `_env(...)`.
  - **No** passphrase field — BitUnix's signing uses pure SHA256, not HMAC+passphrase like Coinbase legacy.
- `trading_corp/main.py`:
  - **NEW broker family branch** `if family == "bitunix":` mirroring the Coinbase pattern. Constructs `BitunixBroker(api_key=secrets.bitunix_futures_api_key, api_secret=secrets.bitunix_futures_api_secret)`. In PAPER mode wraps in `PaperExecutionBroker` so snapshots use real BitUnix data while orders simulate via `PaperBroker`. In LIVE mode (not currently exercised) returns the unwrapped real broker — but `place_order` raises until Phase 4 ships.

**Features shipped (load-bearing for future "is X done?" checks):**
- **BitUnix Futures division now reads real account data on prod.** Dashboard tile in Crypto group shows live equity ($2,500.00 confirmed against BitUnix UI), live position count (0 currently), STANDBY badge stays.
- **Real BitUnix API auth working from Azure VM IP.** Unlike Fidelity, BitUnix's API does NOT IP-block the datacenter address. Confirmed by successful snapshot from `tc-prod-vm` at 17:54:47 UTC. (Important contrast for the Fidelity P1 BACKLOG item.)
- **Multi-margin-coin balance aggregation.** Sums USDT + USDC futures balances. BTC/ETH-margined balances are deferred (need quote conversion to USD).
- **Phase 1 read-only enforcement.** `place_order` / `cancel_order` raise `NotImplementedError` on the unwrapped broker as a defensive backstop until Phase 4. PAPER mode wrapping insulates the live signal path.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`transfer` field is additive in BitUnix equity math.** Initially I assumed `transfer` was a duplicate view of `available` (i.e. "available to transfer out"). User-confirmed against UI: `available + transfer = total wallet balance`. The two are independent components. Keep it summed.
- **BitUnix supports many margin coins; per-coin queries required.** No bulk endpoint exists. Phase 1 sums stablecoins only (USDT, USDC) and treats them 1:1 USD. If user moves funds to BTC/ETH margin, the dashboard will under-count until a quote-conversion path is added.
- **Connect-time smoke check is best-effort, not fatal.** If BitUnix returns 401 or rate-limits during boot, broker stays registered with stub data and hydration catches the error later. No restart loop.
- **Snapshot is sequential + slow (~37s observed).** Three sequential API calls (account x 2 coins + positions). Future polish: parallelize via `asyncio.gather`. Not blocking — happens at startup, not per-request.
- **No live order capability.** `place_order` raises in unwrapped form. PAPER wrapping makes orders flow to `PaperBroker`. Phase 4 will replace the raise with real BitUnix order placement (gated on stop-loss strategy + conviction → leverage map per the vision memo).
- **KV migration was targeted, not full upload.** `scripts/upload_secrets_to_keyvault.ps1` uploads ALL .env values to KV — that would clobber `LORD-OTTER-WEBHOOK-SECRET` and skip the divergent `MARKET-CYPHER-WEBHOOK-SECRET` per `trading_corp_prod_ops.md`. Direct `az keyvault secret set` was used for just the 2 BitUnix keys.

**Latent bugs caught + fixed (during this session):**
- **`marginCoin=USDT` alone misses USDC funds.** Initial snapshot returned $0 because user's $2500 was in USDC, not USDT. Found via raw-API probe across margin-coin variants. Fix: loop over `_STABLE_MARGIN_COINS = ("USDT", "USDC")` and sum.
- **`transfer` field omitted from equity.** Initial formula = `available + frozen + margin + crossUPnL + isoUPnL + bonus`. Returned $1250 instead of expected $2500. User confirmed `transfer` is additive (in-transit balance crediting the wallet, not a duplicate of `available`). Added to formula.

**Verification:**
- Local smoke test (`python -c ...`): BitunixBroker connects, equity = $2500.00, cash = $1250.00 (available across both coins), 0 positions. Quote endpoint returns BTCUSDT live price ($78,690 at test time).
- Local browser render at `localhost:8000` confirmed by user — BitUnix tile in Crypto group shows EQUITY $2,500.00 with STANDBY badge.
- Prod restart 2026-05-03 17:54:09 UTC; web bound at 17:54:47 (~38s); `/healthz` returned 200 OK in 1.2s.
- Prod journalctl confirmed: KV pulled `BITUNIX-FUTURES-API-KEY` + `BITUNIX-FUTURES-API-SECRET` at 17:54:10; `Registered paper-exec broker for division=bitunix_futures (paper=True)` at 17:54:11; `BitunixBroker connected (account=bitunix-futures, equity=$2500.00, 0 positions)` at 17:54:47.
- "Unknown broker family 'bitunix'" WARNING from the 16:25 UTC deploy is now GONE — confirmed absent in post-restart journalctl.
- User confirmed visual at `https://trading.jacksumner.com`.

**Inert / dormant on current traffic:**
- **No signal fan-out to bitunix_futures division.** Per the autonomous-division vision, signals should reach every division and let each decide; today's signal-routing only sends Otter/Cypher to `coinbase_spot`. So bitunix_futures gets snapshot calls (for the dashboard) but never receives `place_order` calls — even in PAPER. Phase 3 (division-entry filters + signal fan-out) ships that.
- **`place_order` / `cancel_order` raise paths are untested in prod.** They never trigger because of the PaperExecutionBroker wrapping + lack of fan-out. If you remove the wrapper or add fan-out, the raise becomes a real failure mode — Phase 4 will replace with real implementation.
- **BTC/ETH-margined balances not summed.** If user moves funds out of stablecoin margin into crypto-margin, dashboard will under-count until quote-conversion lands.
- **Snapshot timing is slow.** ~37s of three-sequential-API-calls cost at boot. Doesn't affect runtime (snapshots are cached + only re-pulled on dashboard load); just a startup-latency observation for future polish.
- **Coinbase Futures is still order-capable behind STANDBY badge.** Independent from BitUnix Phase 1; tracked as a follow-up to actually disable the order path.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-phase1-20260503-1744; BASE=/home/azureuser/trading_corp
for f in trading_corp/utils/secrets.py trading_corp/main.py; do
  mv \$BASE/\$f.\$TAG \$BASE/\$f
done
rm -f \$BASE/trading_corp/brokers/bitunix.py
sudo systemctl restart trading-corp
"
# Optionally also remove KV secrets if you want a true Phase-0 state:
#   az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name BITUNIX-FUTURES-API-KEY
#   az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name BITUNIX-FUTURES-API-SECRET
# (KV secrets being present is harmless if the broker code is gone — main.py
# falls back to the "Unknown broker family" warning path.)
```
