# Polymarket whale workflow — ground-truth verification

**Date:** 2026-06-09 (operator probes + closeout direction 2026-06-09/10)
**Branch:** `polymarket-workflow-verification-2026-06-09` (base `origin/main` `2b782c866aadfe256e1c6dd4cfc36ff110284247`)
**Mode:** read-only investigation. No code changes, no schema changes, no prod writes, nothing disabled.
**Worktree:** `.claude/worktrees/polymarket-workflow-verification-2026-06-09` (isolated, per session discipline).
**Predecessors:** investigation `reports/2026-06-09_polymarket_sell_pairing_investigation.md`;
scoping `reports/2026-06-09_polymarket_option_c_implementation_scoping.md` (the doc whose
behavior claims this session checks against ground truth).
**All file:line citations are from this worktree at `2b782c8`.** Where the scoping doc cited a
line (it was based on `2ed3998`), the citation was re-verified at `2b782c8` and matches unless noted.

---

## 0. Operator's stated workflow (verbatim) + verdict

> "Sunday process populates the watchlist. I manually review and analyze the whales on the
> watchlist and promote some to the whale copy trading list. When promoted, the agent should
> copy trade the whale. Simple. Period. That's it. Nothing more complex. If a whale is on the
> whale list and they trade, we copy it. Period. End of story."

Original explicit constraints:
- **NO auto-promotion.** Promotion is operator-driven via a "promote whale" button.
- **NO auto-demotion / autopause.** If autopause currently runs, operator wants it disabled.
- **Watchlist refresh produces RANKINGS for operator review, not roster membership.**

The scoping doc (§4, §6) claims behavior that contradicts the above:
- `refresh_polymarket_whales` directly populates `selected_whales` (treated as the copy roster).
- `_apply_autopause_filter` runs every ~60s and destructively rewrites `selected_whales`.

**Verdict in one line:** the scoping doc's two specific code claims are **accurate AND active on
prod** (autopause has fired 3× — see §5/Q4b). The operator's *core manual workflow is also fully
implemented and working*: the promote button exists and writes the copy roster, copy-trade reads
it, the Sunday job populates the watchlist. So this is **not** "intent fundamentally not
implemented." The genuine extras are **(b)-class** "code does more than you asked":
**autopause auto-demotes**, and **`refresh_polymarket_whales` is a latent auto-promotion path**.

**Operator direction at closeout (2026-06-09/10):** workflow is **updated** to accept both manual
promote/demote AND system-driven autopause demotions. **Autopause stays enabled.** The remaining
gap — operator visibility into *which* whales the system silently demoted — is filed as a
quality-of-life feature (**BACKLOG P3 — Polymarket whale demotion transparency**), not a disabling.

---

## 1. State verification

- `git rev-parse origin/main` → `2b782c866aadfe256e1c6dd4cfc36ff110284247`. Local `main` at same SHA.
- Dedicated worktree + branch `polymarket-workflow-verification-2026-06-09` created off `main`.
- Schema (from `trading_corp/persistence/db.py`):
  - `agent_state(agent, key, value_json TEXT, updated_ts TEXT ISO-8601)` — db.py:109-115.
  - `audit_event(id, ts TEXT ISO-8601, actor TEXT, kind TEXT, payload_json TEXT)` — db.py:32-39.
  - `set_agent_state`/`load_agent_state` — db.py:458/482 (value stored/read as `value_json`).

---

## 2. Q1 — What populates `selected_whales`?

**Answer: scenario (iii)+ — FOUR distinct writers.** `selected_whales`
(`agent='polymarket_copy_trader'`) is written by all of:

| # | Writer | file:line | Semantics | Auto/manual |
|---|--------|-----------|-----------|-------------|
| 1 | `refresh_polymarket_whales.py` | `scripts/refresh_polymarket_whales.py:285-288` | **Full rebuild** from algorithm top-N (Rule B: top-N per category + top-N global), then **merges `pinned_whales`** (lines 250-282) so dashboard promotions survive. Also writes `selection_metadata` (`:289-292`). | **MANUAL** — no systemd timer / cron runs it (see §5). |
| 2 | Promote endpoint | `web/routes.py:2090` (`polymarket_watchlist_promote`), write at `:2142` | **Appends** promoted wallet (idempotent on wallet) **and pins it** via `pinned_whales` (`:2143`). | Operator button. |
| 3 | Demote endpoint | `web/routes.py:2158` (`polymarket_whales_demote`), write at `:2212` | **Removes** demoted wallet from `selected_whales` + `pinned_whales` (`:2213`); calls `force_close_whale_positions`. | Operator button. |
| 4 | `_apply_autopause_filter` | `agents/strategies/polymarket_copy_trader.py:571-574` | **Destructive rewrite** to the `keep` list on autopause trigger (removes whales). No separate paused-set key — pause = absence from `selected_whales`. | **AUTOMATIC**, every copy cycle (~60s) — see Q4. |

**Direct quotes:**
- refresh write: `set_agent_state("polymarket_copy_trader", "selected_whales", selected_records, db_url=db_url)` (refresh_polymarket_whales.py:285-287). The module header (`:16-17`) states its job is to "Write list of dicts … to `agent_state(polymarket_copy_trader.selected_whales)`."
- pinned merge: refresh_polymarket_whales.py:250-253 — *"Merge manually-pinned whales (promoted via dashboard) into the algorithm's selection so they survive this refresh… Without this step, dashboard promotions would be silently evicted on every refresh run."*
- autopause write: `set_agent_state(self.name, _AGENT_STATE_SELECTED_WHALES, keep, db_url=self._db_url)` (polymarket_copy_trader.py:571-573).

**Resolution of the Q1 expected-truth options:**
- The scoping doc's claim ("refresh writes `selected_whales` directly") is **TRUE** → option **(ii)** for the refresh path: *the doc is correct; the operator's mental model ("refresh produces rankings, not roster") is wrong for the script literally named `refresh_polymarket_whales`.*
- BUT the operator's *promote button* also exists and writes `selected_whales` (option (i)'s mechanism is present too). So the full picture is option **(iii)**: refresh rebuilds, button adds, autopause removes.
- The RANKINGS-for-review roster the operator means is a **different key** (`watch_only_whales`, written by the Sunday deep-seed) — see Q5. So the operator's mental model is correct about *that* pipeline; the confusion is the two similarly-named scripts (`seed_polymarket_watchlist_deep` vs `refresh_polymarket_whales`).

---

## 3. Q2 — What does the copy trader read as its "copy these whales" roster?

**Answer: `selected_whales`, confirmed.** The copy trader is the execution reader.

- `polymarket_copy_trader.py:180` — `selected_whales = self._load_selected_whales()` at the top of
  `run_scan_cycle`.
- `_load_selected_whales` — polymarket_copy_trader.py:611-635 — reads
  `load_agent_state(self.name, "selected_whales")` (`self.name == "polymarket_copy_trader"`, line 104).
- `polymarket_copy_trader.py:185-190` — immediately applies `_apply_autopause_filter` to that list.
- `polymarket_copy_trader.py:196-216` — iterates each surviving whale, fetches their `/activity`,
  and emits copy ProposedOrders (`_process_whale_activity`).

So the chain is exactly: **`selected_whales` → copy trades from those wallets.** There is no
indirection; the copy roster IS `selected_whales`. (Display readers in `web/data.py` also read it —
see Q5 — but those are dashboard rendering, not execution.)

---

## 4. Q3 — Does the "promote whale" button exist, and what does it write?

**Answer: YES — it exists and writes `selected_whales` + `pinned_whales`.** The operator's stated
promote workflow is fully implemented.

- Endpoint: `POST /api/polymarket/watchlist/promote/{proxy_wallet}` →
  `polymarket_watchlist_promote` (web/routes.py:2090).
- Reads the whale's `user_name`/`category` from the existing `watch_only_whales` entry
  (routes.py:2108), then:
  - appends to `selected_whales` (idempotent on lower-cased wallet) — write at routes.py:2142;
  - appends to `pinned_whales` — write at routes.py:2143 (this is what protects the promotion from
    being evicted by a later `refresh_polymarket_whales` run; see Q1 #1 pinned-merge).
- Emits a `polymarket_whale_promoted` audit (routes.py:2147).
- The design comment at routes.py:1959-1969 states the contract explicitly: *"Promote moves a whale
  from `watch_only_whales` into `selected_whales` AND pins it via `pinned_whales` (so the next
  `refresh_*_whales.py` run doesn't evict it)… Strategy reloads `selected_whales` every cycle so the
  change takes effect [next cycle]."*
- Symmetric demote endpoint: `POST /api/polymarket/whales/demote/{proxy_wallet}` (routes.py:2158);
  emits a `polymarket_whale_demoted` audit (routes.py:2217).

**So the operator's "promote whale button" is real, and the manual promote→copy path works as the
operator believes.** This is the load-bearing reason the situation is NOT "operator intent
fundamentally not implemented."

---

## 5. Q4 — Does `_apply_autopause_filter` actually run?

### 4a — Code path (CONFIRMS the scoping doc claim)

- **Definition:** `_apply_autopause_filter` — polymarket_copy_trader.py:526. (Scoping doc cited `:526`
  and `:571`; both match at this SHA.)
- **Call site:** polymarket_copy_trader.py:185 — invoked **unconditionally** inside `run_scan_cycle`,
  right after loading the roster. There is **no separate autopause feature flag**; the only gate is
  the whole agent's `enabled` (checked at `:177`).
- **Trigger:** `should_autopause` (`_whale_autopause.py:69`) over `polymarket_round_trips`, conjunctive
  thresholds `n_resolved >= 30 AND win_rate < 40% AND total_realized_pnl < -$5`
  (`_whale_autopause.py:26-28, 96-101`).
- **Effect:** on any trigger, destructively rewrites `selected_whales` to the `keep` list
  (polymarket_copy_trader.py:571) and emits `polymarket_whale_auto_paused`
  (polymarket_copy_trader.py:589-606). Pause is expressed **only** as absence from `selected_whales`
  (no `auto_paused_whales` key exists — the scoping doc's F-4 *proposes* adding one; it is not in code).
- **Cadence:** the loop `_scheduled_polymarket_copy_trader_loop` (main.py:3249) sleeps
  `max(15.0, poll_interval_sec)` with `poll_interval_sec` default **60** (main.py:3269-3270), then calls
  `run_scan_cycle` (main.py:3286). So: **every ~60s, autopause runs and can rewrite `selected_whales`** —
  exactly as the scoping doc claims — **whenever the agent is `enabled`.**

**Verdict 4a: the scoping doc's "runs every ~60s, destructively rewrites `selected_whales`" is
code-accurate.**

### 4b — Operational state on prod: AUTOPAUSE IS ACTIVE (operator probes, 2026-06-09/10)

The repo could not settle this alone (the committed `config/strategies.yaml` has **no
`polymarket_copy_trader` block**, so `enabled` defaults `False` — polymarket_copy_trader.py:128/131,
141-144 — yet prod has copy-trade history). **Operator-run SSH probes resolved it:**

- **Autopause is ACTIVE on prod.** The copy-trader agent is `enabled` on prod (prod's live
  `strategies.yaml` differs from the committed repo file, which has no block), so `run_scan_cycle`
  + `_apply_autopause_filter` execute every ~60s as in 4a. This is outcome **(i)** of the three
  possibilities flagged pre-probe.
- **Three autopause events recorded:** **2026-05-15, 2026-06-03, 2026-06-09** (`audit_event` rows of
  kind `polymarket_whale_auto_paused`). The system has silently demoted whales on those three dates.

This directly contradicts the operator's *original* "NO auto-demotion" constraint — but per the
closeout direction (§0), the operator now **accepts** autopause and instead wants **visibility** into
these events (filed as BACKLOG P3, see §7).

---

## 6. Q5 — Key map for the Polymarket whale pipeline

All keys under `agent='polymarket_copy_trader'` unless noted.

| Key | Writer(s) (file:line) | Reader(s) (file:line) | Operator-facing semantics |
|-----|-----------------------|-----------------------|---------------------------|
| **`selected_whales`** | refresh_polymarket_whales.py:285 (rebuild); routes.py:2142 (promote, add); routes.py:2212 (demote, remove); polymarket_copy_trader.py:571 (autopause, remove) | **EXECUTION:** polymarket_copy_trader.py:180/611 (copy roster). **DISPLAY:** web/data.py:4429, 4604, 4657, 4840 (panels + watch-list filtering) | **The copy / execution roster.** Whales here get copy-traded. |
| **`watch_only_whales`** | seed_polymarket_watchlist_deep.py:690-691 (weekly merge); `*_metadata` at :694-695. **Not** mutated by promote/demote (routes.py:2205-2208 says so). | **DISPLAY only:** web/data.py:4810-4840 (Watch List panel; entries already in `selected_whales` are filtered out). | **The observation watchlist / RANKINGS for operator review.** This is what the operator promotes *from*. Matches the operator's mental model of "the watchlist." |
| **`pinned_whales`** | routes.py:2143 (promote, add); routes.py:2213 (demote, remove). | refresh_polymarket_whales.py:255-256 (merged into rebuild so promotions survive); web/data.py:~3531-3536 (📌 pin indicator). | **Manual-promotion protection set.** Decouples operator promotions from the algorithm's rebuild. |
| **`selection_metadata`** | refresh_polymarket_whales.py:289-292. | Dashboard "last refresh" display (web layer); no execution reader. | Last `refresh_polymarket_whales` run summary (filters, counts, pinned_merged, timestamps). |
| **`watch_only_whales_metadata`** | seed_polymarket_watchlist_deep.py:694-695. | Dashboard display; no execution reader. | Last deep-seed run summary. |
| **`whale_state:<wallet>`** | polymarket_copy_trader.py:654 (`_save_whale_state`); :754 (`force_close_whale_positions` resets on demote). | polymarket_copy_trader.py:637-649 (`_load_whale_state`). | Per-whale runtime cursor (`last_seen_ts`, `our_positions`, `last_seen_txhashes`). Not operator-facing. |
| **`auto_paused_whales`** | — does NOT exist — | — | Proposed by scoping doc F-4; **not implemented.** Pause is currently expressed only as absence from `selected_whales`. |

**Demotion-class audit events (relevant to the P3 transparency feature):**
- Autopause demotion → `polymarket_whale_auto_paused` (polymarket_copy_trader.py:589).
- Manual demotion → `polymarket_whale_demoted` (routes.py:2217) — **already emitted today.**
- Manual promotion → `polymarket_whale_promoted` (routes.py:2147).
- So a demotions dashboard can union `polymarket_whale_auto_paused` + `polymarket_whale_demoted`; the
  manual-demotion audit event the P3 entry asks to "verify" **already exists**.

**Scheduling (systemd, from `infra/systemd/`):**
- `trading-corp-pm-watchlist-deep.timer` → **Sun 13:00 UTC weekly** → `seed_polymarket_watchlist_deep
  --merge --max-total 100` → writes `watch_only_whales`. **← the operator's "Sunday process populates
  the watchlist."**
- `trading-corp-pct-pruner.timer` → daily 11:30 UTC → `prune_stale_pct_entries` → deletes stale pending
  BUY **audit rows** (>24h, unpaired). **Does not touch the roster** (not a `selected_whales` writer).
- **No timer or cron runs `refresh_polymarket_whales`** — it is manual-only. (Grep over `infra/systemd/`
  and the tree found no scheduler reference; only docstrings/comments/docs mention it.)
- The copy trader loop runs in-process under `trading-corp.service` (main trading process), ~60s cycle.

---

## 7. Synthesis

### B1 — Behavior diff (operator intent vs. code)

| # | Operator's *original* intent | Code's actual behavior | Match? | Severity / disposition |
|---|------------------------------|------------------------|--------|------------------------|
| 1 | Sunday process populates the watchlist | `seed_polymarket_watchlist_deep` via `trading-corp-pm-watchlist-deep.timer`, Sun 13:00 UTC weekly, writes `watch_only_whales` | **MATCH** | — |
| 2 | Operator manually promotes via a "promote whale" button | `polymarket_watchlist_promote` (routes.py:2090) writes `selected_whales` + `pinned_whales` | **MATCH** | — |
| 3 | Copy trader copies whales on the whale list | reads `selected_whales` (polymarket_copy_trader.py:180), copies their `/activity` (`:196`+) | **MATCH** | — |
| 4 | **NO auto-promotion** | `refresh_polymarket_whales` rebuilds `selected_whales` from algorithm top-N — but is **manual / unscheduled**; `pinned_whales` protects button-promotions from it | **PARTIAL MISMATCH** | **structural (latent)** — no scheduled auto-promotion today, but a manual tool can wholesale-rebuild the roster; main.py:955-958 frames it as the intended "quarterly" populator |
| 5 | **NO auto-demotion / autopause** | `_apply_autopause_filter` runs every ~60s and removes whales from `selected_whales`; **ACTIVE on prod, fired 2026-05-15 / 06-03 / 06-09** | **MISMATCH (vs original)** | **operational — RESOLVED by operator decision:** autopause is now **accepted**; the gap becomes a *visibility* need → P3 |
| 6 | Watchlist refresh = RANKINGS for review, not roster | Two scripts: `seed_polymarket_watchlist_deep`→`watch_only_whales` (rankings = MATCHES intent) **but** `refresh_polymarket_whales`→`selected_whales` (execution roster = does NOT match) | **PARTIAL MISMATCH** | **cosmetic→structural** — naming collision; the literal "refresh" script writes the roster |

### B2 — Findings classification (post-decision)

- **(a) Code does what operator wants; doc misdescribed:** none. The scoping doc's two specific claims
  are both code-accurate and active.
  - *Sub-note:* the **mental model** that was wrong was the operator's, not the doc's — the operator
    believed "refresh produces rankings, not roster," but `refresh_polymarket_whales` writes the roster
    (`selected_whales`). The operator's model IS right for the *other* script,
    `seed_polymarket_watchlist_deep` → `watch_only_whales`.
- **(b) Code does something not in the original ask:**
  - **(b1) Autopause auto-demotion** — `_apply_autopause_filter` (polymarket_copy_trader.py:185, 526-607,
    571), **active on prod**. **Operator decision: ACCEPTED — stays enabled.** Reclassified from
    "disable" to "make visible." → drives the P3 transparency feature.
  - **(b2) `refresh_polymarket_whales` as a latent auto-promotion path** — wholesale-rebuilds
    `selected_whales` from the algorithm (refresh_polymarket_whales.py:285). Latent because unscheduled;
    `pinned_whales` protects button-promotions. **No operator action requested this session;** left as a
    known latent tool.
- **(c) Operator wants something the code doesn't do:**
  - **Demotion transparency** (dashboard surface of recent system/manual demotions + promote-guard
    showing prior demotion history). This is new operator-facing functionality → **BACKLOG P3 — filed.**

### B3 — Recommended actions / disposition (no code changed this session)

- **(b1) Autopause:** **No disabling.** Operator accepts it. Build **demotion transparency** instead
  (BACKLOG P3 — Polymarket whale demotion transparency on dashboard). Source data already exists:
  `polymarket_whale_auto_paused` (autopause) + `polymarket_whale_demoted` (manual) audit events — a
  dashboard query unions them; the promote-button guard queries demotion history for a wallet before
  write. Not gating any active development.
- **(b2) `refresh_polymarket_whales` latent auto-promotion:** flagged, no action requested. If it is
  ever scheduled, it would clobber non-pinned roster state and the autopause-vs-refresh flap (scoping
  doc §6) reopens. Also: main.py:955-958 comment misframes refresh as the intended quarterly populator
  (doc/comment drift) — low-priority cleanup.
- **Nothing disabled, no code/schema changed.** Read-only verification only.

---

## 8. Operator SSH probes (read-only) — executed; results in §5/Q4b

Three short scratch scripts (`v1.sh`/`v2.sh`/`v3.sh`, workspace root) streamed via
`Get-Content vN.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r\357\273\277'|bash"`
(all SELECT / grep / journalctl / systemctl-list — strictly read-only). Outcome (operator-run):
copy trader **enabled on prod**, autopause **active**, **3** `polymarket_whale_auto_paused` events
(2026-05-15, 2026-06-03, 2026-06-09); `refresh_polymarket_whales` confirmed **not scheduled**.

---

## 9. Session closeout (2026-06-09/10)

- **Ground truth established:** scoping-doc behavior claims are code-accurate AND active on prod;
  operator's core manual workflow (Sunday watchlist → promote button → copy) is fully implemented.
- **Operator direction:** autopause is **accepted** and **stays enabled**; the operator's workflow
  now explicitly includes both manual and system-driven demotions. Transparency need → **BACKLOG P3**.
- **Option (c) F-decisions reconciled** with this ground truth (recorded in the option-(c) scoping
  memory entry): F-1 stands; F-3 simplified to light-touch validation; **F-4 changed** — the proposed
  `auto_paused_whales` separate-key design is unnecessary while `refresh_polymarket_whales` stays
  unscheduled (no flap; reopens only if refresh is ever scheduled); F-5 stands.
- **No code, schema, or prod state changed.** This is a planning/verification artifact, committed
  unmerged on `polymarket-workflow-verification-2026-06-09`, mirroring the investigation + scoping
  pattern.
