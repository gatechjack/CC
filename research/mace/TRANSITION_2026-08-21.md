# MACE Phase-1 — Transition Document for the Next Code Agent

_Written 2026-08-21 ~00:55 ET. Facts verified against the live box DB / repo this session, not recited from memory. Where this contradicts a prior verbal framing, the verified state below wins._

---

## 1. LIVE STATE SNAPSHOT

- **prod-live tip = `7150404`** — **NOT advanced this session** (gate 7 deliberately held; see the P1.5 blocker in §2).
- **The BOX runs the full P1 code**, deployed off the `mace-phase1-fix-2026-08-20` branch:
  - `config_hash 3256226747af` (was `65d85cc4b4cc`), `rung_risk_pct 0.14`, `risk_band_max_usd 550`, `min_strike_separation_usd 0.0`, `strike_band_pct 0.25`, `max_contracts 1`, `weekly 5`, universe 6-active `[IBIT,XLE,GDX,FXI,IWM,SPY]`. **ARMED** (halt latch `halted:false`, `auto_execute:true`). Engine PID `809127`, `NRestarts=0`, web `:8000` 200 (flaky-slow off-hours under kalshi/poly load — environmental, not P1).
  - **★ BOX-vs-GIT DIVERGENCE (by design):** the box runs P1, but **prod-live git-truth still points at `7150404`**. The box is AHEAD of prod-live. This gap is intentional and gated on P1.5.
- **Branch:** `mace-phase1-fix-2026-08-20` @ **`0b85288`** (`0b8528832ea030c489459d575ca862d73aa388bb`). Two commits: **`4b1add4` = P1.2** (neutral `MaceOrderRejected` clean stand-down), **`0b85288` = P1.1 + P1.3** (collision shift + snap-to-grid + config). MACE suite **313 passed**. Worktree `cc-mace-phase1-wt`. **NOT pushed** (not on origin).
- **★ config.py CORRECTION (verify — this contradicts an earlier framing):** config.py was **omitted from the FIRST box swap** (a deploy process error — 6 of 7 runtime/config files swapped), then **DEPLOYED on the second restart** (809127). Verified now: box `config.py` has the `min_strike_separation_usd` field (4 occurrences on-box) and loads `min_sep 0.0` with no `AttributeError`. **So the box config.py is CURRENT, not stale.** The process lesson stands (see §6): the deploy must diff the *staged file-set against the branch changed-file-set*.
- **Open book after the phantom clear:** **4 real SPY rungs, 0 submitting, 3 abandoned**; `sum_open_max_risk = $830.00`.
  - `mace-SPY-2026-09-25-742-739-802-805-20260812`
  - `mace-SPY-2026-09-25-746-743-807-810-20260813`
  - `mace-SPY-2026-09-30-740-737-802-805-20260817`
  - `mace-SPY-2026-10-02-734-731-797-800-20260818`
  - Abandoned (no capital, RH-verified): the 8/19 collision phantom + the 2 off-hours catch-up phantoms (`GDX 88.5/83.5/115/120`, `SPY 729/726/791/794`).

**★ The P1 code is LIVE + ARMED on the box.** The **15:45 ET eval will place real P1 trades** (likely GDX's first non-SPY $5-wide condor). If that is not desired before P1.5 is fixed, HALT MACE (kill-switches in §7). Jack chose to keep the verified P1 code running.

---

## 2. THE BLOCKER — P1.5 (gates the prod-live advance)

**Off-hours catch-up bug (pre-existing; EXPOSED, not introduced, by P1):** a MACE restart **after 15:45 ET** runs the daily-slots ENTRY catch-up for that session — on a fresh boot `fired` is empty and `now.time() >= 15:45` is true, so the entry slot fires. The `entry_cutoff_et` (15:58) stand-down in `run_entry` compares **time-of-day only** (`now_t >= cutoff`), which **fails after midnight ET** (`00:06 < 15:58`) → it attempts a **stale-session off-hours entry** with `auto_execute:true`.

**Proven live 2026-08-21:** the first P1 restart (23:45 ET 8/20) fired an entry for session `2026-08-20` at 04:06 UTC. The P1 code correctly built GDX $5-wide (`88.5/83.5/115/120` credit 1.65) and SPY shifted (`729/726/791/794` credit 0.96) and **tried to place both**. Both hit `database is locked` (SQLite contention during the ~15-min busy boot) → `placed=0`. **RH read-only VERIFIED zero orders / zero fills** — the DB-lock was a lucky near-miss that prevented a real off-hours placement at stale/post-close quotes.

**Required fix:** the ET-guard (currently only blocks the 15:40–15:58 restart window) must **also stand down post-15:45 / stale-session restarts** — i.e. the daily-slots catch-up must not fire for a session whose entry cutoff has already passed, and/or `run_entry`'s cutoff must be **session-date-aware** (not time-of-day-only across midnight). Bake ≥1 live day. **This MUST be fixed and baked before prod-live advances to the P1 commit.**

**★ OPERATIONAL CONSTRAINT until P1.5 is fixed:** **NO MACE restart after 15:45 ET on any day** (would re-trip the catch-up). Band-widen/weekly5 avoided it by restarting before 15:45 ET.

---

## 3. DEPLOY DEBT (do with P1.5, before advancing prod-live)

- **config.py: ALREADY DEPLOYED** (2nd restart, verified this session — see §1 correction). No outstanding config.py action.
- **Remaining:** (a) build + bake **P1.5**, then (b) **advance prod-live** to the P1 commit (blob-proof `config_hash` at tip == box) + **minimal-additive main sync** (see §4).

---

## 4. GIT TRAPS (verbatim — these bite every time)

- **main vs prod-live is a `BACKLOG.md` document-identity fork** — NEVER a clean fast-forward. Sync main **minimal-additive only** (take the delta, hand-union BACKLOG/deploy_log).
- **FORK DEBT: main is behind prod-live by 7 poly-kalshi commits** (`570d6fc..7150404`). This is deferred to its OWN dedicated session — **do NOT drag it into a MACE deploy.** MACE reaches main via minimal additive re-apply.
- **DRIFT-GATE on Windows/git-bash:** local `git cat-file blob <c>:<f> | md5sum` **mangles line-endings → false drift** (bit main.py twice). Authoritative check = **ON-BOX content diff, LF both sides**: `git cat-file blob <c>:<f> | ssh $H "tr -d '\r' | diff - <boxpath> | wc -l"` (0 = identical).

---

## 5. REMAINING P1 PIPELINE (each bakes ≥1 live day — NO same-night stacking)

- **P1.4 — `manage_tick` None-tolerance** (independent robustness track). On the 2026-08-20 RH-session outage the manage loop crashed **80×** (`'NoneType' object has no attribute 'get'`) because it didn't tolerate None broker responses. Guard the manage tick to skip+log on a None quote/response instead of per-rung crashing. Independent of P1.5; can proceed in parallel.
- **After P1 fully bakes → Phase 2:** broker-agnostic, universe-scanning iron-condor engine. **Design-first** — open inputs Jack must set: scanner universe, broker set, pacing. Do NOT start building without a ratified design.

---

## 6. STANDING FLAGS (recorded for Jack — the next agent should NOT action these unprompted)

- **Deploy-process hardening (the real root cause of every scare this week):** add a **pre-drift-gate check that the staged file-set == the branch changed-file-set** (would have caught the config.py omission); investigate the **~15-min off-hours boot** (env: kalshi/poly ConnectTimeout, EODHD 404 BTC/USD, fidelity playwright firefox-lock; systemd `TimeoutStartUSec=90s` logs a benign `'timeout'` result that does NOT restart-loop — `NRestarts` stays 0); **script the gate battery** (drift/backup/swap/verify) so steps can't be skipped. The PROCESS, not the P1 code, caused the incidents.
- **Agent-driven prod restarts** via locally-authed `az vm run-command` (root, no sudo) are now the de-facto norm vs the documented operator-run paste-runner rule — **Jack to bless or reinforce**.
- Memory file `areas/options-5k-atomic.md` near its size cap — **consolidate**.
- **Single-symbol concentration** up to ~$1,675 (55% of the 0.95×E cap) is possible at `rung_risk_pct 0.14` with no per-symbol dollar cap — a future guard candidate.
- **XLE** still needs the Phase-2 data-layer fix (degenerate RH greeks on thin/zero-bid OTM strikes; band-widen + snap-to-grid do NOT fix it).
- Carried opens: **GDX Dec-refresh tripwire ~12/1**; **OPEC blackout tripwire** (re-source after each meeting); **KeyVault security backlog (item j)**; **backtest still deferred**.

---

## 7. HYGIENE / SAFETY RAILS FOR THE NEXT AGENT

- **Preserve the rollback backup on the box — do NOT delete:** `/home/azureuser/mace_phase1_bak_20260821_034245/` (`rollback.sh` restores the 6 runtime files to `65d85cc4b4cc`; `config.py.PREEXISTING_OLD` restores the old config.py separately). ET-guarded. Rollback does NOT re-create the abandoned phantoms.
- **Do NOT delete the `mace-phase1-fix-2026-08-20` branch** — Jack decides deletion **after prod-live advances**; verify reachability from a mainline first (b51ddc6 orphan-lesson).
- **Protect the `cc-2026-08-02-wt` lab worktree DB** (228 MB) — do not touch.
- **Jack retains ALL deploy/promotion decisions.** Kill-switches are hot from the browser: `strategies.yaml auto_execute:false` (halt new entries), `divisions.yaml standby:true` (halt scan+manage), or remove `robinhood_mace` from the systemd `--live-divisions` + restart (full disarm). Restart mechanism = `az vm run-command` (root, ~2.5–15 min boot; AVOID after 15:45 ET until P1.5).

---

### One-line handoff
P1 code is **live + ARMED + verified-correct on the box** (GDX $5-wide proven), prod-live git **held at 7150404**; the single blocker to advancing is **P1.5** (off-hours catch-up guard); build P1.5 (+ optionally P1.4 in parallel), bake, then complete gate 7 (advance prod-live + minimal-additive main sync). No MACE restart after 15:45 ET until P1.5 lands.
