# Deploy-log entry template — DB lock-storm fix (Tier-1 + #3 checkpointer isolation)

Paste into `runbooks/deploy_log.md` at deploy time and fill the blanks. Do NOT deploy without
the pre-flight boxes checked. See `reports/2026-07-10_db_lock_storm_diagnosis.md` for the why.

---

## 2026-07-__  DB lock-storm fix (checkpointer isolation + synchronous=NORMAL)

- **Branch:** `db-lock-contention-fix-2026-07-10` @ `__COMMIT__` (rebased on origin/main d9c32de; NOT merged at deploy)
- **Runner:** `scripts/deploy/deploy_db_lock_fix.ps1` (phase 1 = transfer default; phase 2 = `-Restart`)
- **Diagnosis + root cause:** `reports/2026-07-10_db_lock_storm_diagnosis.md`
- **Scope:** stop the storms (Tier-1 + #3). Fix #4 (agent_state retry) and #5 (write-off-loop) are SEPARATE ships, gated on the 72h result below.

**Files (content md5, CR-stripped) baseline -> target:**
- `trading_corp/persistence/checkpointer.py`  `953ce717` -> `f33b896e`
- `trading_corp/persistence/db.py`            `9cb0f654` -> `bc3df1c8`
- `trading_corp/main.py`                       `b741e95f` -> `b80dc6ce`

**Pre-flight (all required):**
- [ ] RH pickle refreshed — was 2026-07-07 (>20h) at diagnosis; run `rh_pickle_refresh.ps1` (2FA) unless confirmed valid
- [ ] audit-drain cron present (`crontab -u azureuser -l | grep replay_audit_event_write_failed`)
- [ ] prod baseline md5 matched the 3 files (runner aborts on drift — confirms no old-code clobber)
- [ ] quiet window — no in-flight PMCC HITL approval (old suspended threads in the shared `checkpoints` table do NOT migrate; external reconciler + canary catch any orphan, but time the cutover to avoid it)

**Deploy:**
- [ ] phase 1 transfer OK — 3 files md5-verified, backups at `*.pre-db-lock-fix-YYYYMMDD`
- [ ] phase 2 `-Restart` OK
- [ ] POST(a) shared DB (via app connect): `journal_mode=wal` / `synchronous=1(NORMAL)` / `busy_timeout=5000` (5s is BY DESIGN pending #5)
- [ ] POST(b/c) `data/checkpoints.db` present, `journal_mode=wal`, recent mtime (checkpointer moved to its own file)
- [ ] POST(d) boot clean — 0 `database is locked`, no tracebacks (fidelity paper-broker noise excepted)

**72h EMPIRICAL VERIFICATION GATE — through 2026-07-13 (the trigger for #4/#5 decisions):**
- Pre-deploy baseline (40-day trend): **~4–18 lock-contention events/day, 100% exhaustion.**
- Watch daily:
  - journal lock markers: `journalctl -u trading-corp --since '1 day ago' | grep -c 'database is locked'`
  - fallback file: `ls -la data/audit_event_write_failed.jsonl 2>/dev/null` (should stay ABSENT/empty — the hourly cron drains it)
  - archives not growing: `ls -la data/*.replayed-* 2>/dev/null` (a new one each hour with content = still failing)
- **PASS → mark SHIPPED:** lock markers ~0/day; fallback empty/trickle. Then #4 becomes the next (separate) ship; #5 stays deferred.
- **FAIL → fix incomplete:** still accumulating near pre-deploy rate. Escalate to **#5 (offload blocking writes off the event loop)** — the post-#3 diagnosis is cleaner (checkpointer eliminated as a variable). Do NOT bundle #4 into the retry — keep the signal clean.

**Rollback:** restore `*.pre-db-lock-fix-YYYYMMDD` over the 3 files + restart. Reverting `main.py` re-points the saver at the shared DB; `data/checkpoints.db` can be left in place (harmless) or removed when the engine is flat.

**Outcome:** __________  (SHIPPED / REVERTED / MONITORING)  — by ____ on 2026-07-__
