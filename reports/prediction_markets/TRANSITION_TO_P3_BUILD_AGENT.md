# TRANSITION TO THE PHASE 3 BUILD AGENT — Prediction Markets P2 → CP2 Phase 3 (drill-through)

*Standalone handoff. Assume you have read nothing else. Written 2026-08-24 by the CP2 Phase-1/2 build agent.*

---

## ⛑ PICK UP HERE

**STATUS:** CP1 done. CP2 **Phase 1** (pm_web `/healthz`) and **Phase 2** (the whale scoreboard) are **built, box-scratch-green, deployed, and LIVE in a browser** at `https://predictions.jacksumner.com` behind Authelia, gated to `user:jack`. **CP2 Phase 3 (drill-through) is NOT started.** Your job is Phase 3, then the CP2 HALT.

**⛔ HARD GATE — NO DEPLOY UNTIL AFTER MARKET CLOSE (end of day), on Jack's explicit go.** The market is opening. **Build and box-scratch freely** (isolated `~/pm_p2_scratch`, prod untouched) — but the live deploy to the box waits for Jack. Do not deploy pm_web, do not restart anything, until Jack says so at end of day.

**FIRST ACTS, IN THIS ORDER:**
1. Read this whole doc.
2. Read `reports/prediction_markets/P2_PLAN.md` (the board-approved P2 spec).
3. Read `reports/prediction_markets/P1_PLAN.md` **with all amendments** (§13, §13A) — the data model + the §3A scoreable predicate + the ROI definition live there.
4. Read `PLATFORM_VISION.md` (the data-layer re-arch vision; know where P2/P3 sit).
5. Also read `reports/prediction_markets/P2_KICKOFF_2026-08-23.md` (the CP1/CP2 execution record) and `reports/prediction_markets/OPS_GOTCHAS.md` (the three deploy gotchas, full).
6. **CONFIRM THE CURRENT BRANCH STATE WITH JACK IN CHAT before any commit — never a remembered/doc SHA.** The P1 build was nearly branched off a stale base; the P2 agent caught a base-vs-deploy-anchor collapse that *every governing doc had gotten wrong* (see "What went wrong", below). Trust `git ls-remote`, and Jack's word, not a SHA written anywhere — including this doc.

**Where the code + spec live:** everything is reachable on the **durable branch `prediction-markets`** (this doc, `P2_PLAN.md`, `P1_PLAN.md`, `P2_KICKOFF`, `OPS_GOTCHAS.md`, the `trading_corp/prediction_markets/` package incl. `web/`, the tests, and the banked runners under `reports/prediction_markets/runners/`). The P1 and P2 handoffs worked because the spec lived on the branch, reachable — not in an agent's context. Read from the branch.

---

## WHAT PHASE 3 IS

**Drill-through: every aggregate on the scoreboard reaches its rows.** The scoreboard (Phase 2) shows one row per `(wallet, category)`; Phase 3 makes each number a link to the underlying rows that produced it:

- **scoreboard row → the whale's "why ranked"** (the score components: wilson_lcb × edge_factor, the params_json exclusion note).
- **`n_resolved` → the scoreable rows** (the `pnl_suspect = 0` rows for that wallet+category).
- **`avg_win_price` → the won rows, by avg_price.**
- **`two_sided` → the both-outcome condition_ids** (the condition_ids the whale held on >1 `outcome_index`).
- **`single_game` → the single-game-classified rows.**
- **`data_quality` → the quarantined rows, each with its `suspect_reason`** (`row_invariant` | `event_group` | `no_cost_basis`).

**ONE shared row renderer:** `trading_corp/prediction_markets/web/templates/partials/pm_position_rows.html`, serving **both** the product drill-through now **and** the later diagnostics view. Do not fork it.

**Drill-through counts MUST RECONCILE with the aggregate they came from.** If a scoreboard cell says `n_resolved = 469`, the link for that cell must return exactly 469 rows. A drill-through that doesn't reconcile is a bug, not a display detail — build a test that asserts the reconciliation (this is where a Phase-3 halt-and-report checkpoint belongs; see below).

### The display-name item (Jack's feedback from eyeballing the live page — fold into Phase 3, do NOT build separately)
Jack eyeballed the live scoreboard and found what no test caught: **every row is a truncated hex wallet.** He has spent this entire build reasoning about **Kickstand7, BetMechanic, SDTrading, Kh4mz4t** — and none of those names appear on the page meant to support exactly that reasoning. The data already exists: **`pm_whale.user_name`** (P1 schema). This is a **display join, not new data and not a new fetch.** Two hard requirements:
- **SHOW BOTH.** Display name as the primary label, **wallet still visible** (truncated is fine). Wallet is the identity and names CHANGE — the legacy division keyed idempotency on wallet precisely because display names get edited. **Name for recognition, wallet for truth.**
- **HANDLE MISSING HONESTLY.** Not every whale has a `user_name`. Show the wallet; **never a blank, and never a placeholder that reads like a name.** (Same discipline as `single_game n/a` — an honest gap beats a fabricated label.)

**Then the CP2 HALT.** No farm-league page, no search, no diagnostics tab, no paper tables — those are CP3/CP4 and later.

---

## CURRENT STATE — exact and checkable (but confirm with Jack, never trust a SHA here)

**Branches (via `git ls-remote origin` 2026-08-24):**
- **main = `2c8aa23`** — UNTOUCHED since before P2. This invariant was asserted on every commit. Do NOT merge to main until P3 cutover.
- **durable `prediction-markets`** — the handoff branch. As of this doc it is being fast-forwarded to include all P2 CP1+CP2 work + this doc. Re-confirm its exact tip with Jack.
- **phase branch `prediction-markets-p2-2026-08-23` = `08d2d9e`** — the P2 build branch (CP1 + CP2 Ph1/Ph2 code, tests, docs, runners). FF-merges to durable.
- **prod-live = `9c5eb7f`** — the DEPLOY-ANCHOR: records exactly the artifacts deployed on the box (CP1's 3 migration files + CP2 Ph1's pm_web + CP2 Ph2's scoreboard, 8 files, each byte-identical to the box). **prod-live is a deployed-artifact ledger, NOT an approval gate; it advances for deployed artifacts only.** ★ BASE (durable) and DEPLOY-ANCHOR (prod-live) are DISTINCT ROLES — do not conflate them (P1 docs did; see below).

**On the box (`tc-prod-vm`, host `trading.jacksumner.com`, user `azureuser`):**
- **The PM package is at the DOUBLE path `~/trading_corp/trading_corp/prediction_markets/`** (repo-root layout: `~/trading_corp` is the repo root, `PYTHONPATH=~/trading_corp`, CWD of pm_web = `~/trading_corp`). **NOT** `~/trading_corp/prediction_markets` (that single path is inert — a botched deploy created one; it's cleaned). Prove the path with import-resolution before any restart (GOTCHA 3).
- **pm_web:** `prediction-markets-web.service` (systemd, `User=azureuser`, `WorkingDirectory=~/trading_corp`, `PYTHONPATH=~/trading_corp`, `PM_WEB_HOST=127.0.0.1`, `PM_WEB_PORT=8081`, `ExecStart=…/venv/bin/python trading_corp/scripts/pm_web.py`). Bound **loopback-only 127.0.0.1:8081** — reachable ONLY via Caddy. `/healthz` returns 200 + `pm_db_schema_version`.
- **PM DB:** `~/trading_corp/data/prediction_markets.db` (repo-root `/data`, NOT the package). **schema_version = 4.** Row count was **28,319** at CP1; it has grown via the daily refresh (the live page showed a refresh at 03:20 UTC 2026-08-24) — **re-verify the count read-only.** DB is `azureuser:644` (writable by the azureuser runtime — GOTCHA 1).
- **Cron:** the P1 **daily** `20 3 * * *` (03:20 UTC) `refresh --cap 50000` in the azureuser crontab is still active and running (that's the freshness you see). ⚠ **Board Ruling A says replace it with a WEEKLY refresh (Sun 09:00 UTC) — this is a PENDING P2 build item, not yet done.** Don't touch `ingest.py`; the weekly swap is a crontab change + a separate ~30-min paper-entry poll (Ruling A carve-out).
- **Engine:** `trading-corp.service` MainPID **850993** — the live trading engine, UNTOUCHED all through P2. It has NO coupling to the PM package (pm_web is standalone). Do not edit engine files, do not restart it.
- **Caddy (`/etc/caddy/Caddyfile`, Jack's hands):** three vhosts — `trading.jacksumner.com`→`localhost:8000` (engine dashboard, behind Authelia), `auth.jacksumner.com`→`localhost:9091` (Authelia UI, public), and now **`predictions.jacksumner.com`→`localhost:8081`** (pm_web, `forward_auth localhost:9091` mirroring trading, `@public { path /healthz }` bypassing auth). No global-options block, no imports, no snippets, no wildcard host; each vhost auto-issues its own Let's Encrypt cert. DNS: `predictions`/`trading`/`auth` all → `172.171.189.116`.
- **Authelia (`/etc/authelia/configuration.yml`, Jack's hands):** `default_policy: deny`; rules: `trading.jacksumner.com → two_factor subject 'user:jack'` and `predictions.jacksumner.com → two_factor subject 'user:jack'`. Session cookie on the **apex `jacksumner.com`** (SSO across subdomains); **in-memory sessions** (no Redis → every `systemctl restart authelia` logs everyone out; TOTP enrollment survives in `/var/lib/authelia/db.sqlite3`); `access_control` changes need a **restart** (CanReload=no). Single user `jack` in group `admins`.
- **Migrations:** 004 applied (schema 4). **005–007 are reserved** in `db.py` (005 = `pm_paper_trade.size_basis` = FIXED CONTRACT/SHARE COUNT, not dollars — see e7). Phase 3 (drill-through) is a READ feature and should need **no new migration** — the data it drills to already exists (`pm_closed_position`, `pm_category_stats`, `pm_category_onesided_stats`, `pm_score_snapshot`, `pm_whale.user_name`).

---

## ALL LOCKED DECISIONS — with their reasons (a decision without its reason gets relitigated)

**The four board rulings (do NOT relitigate):**
- **A — WEEKLY full refresh, NOT nightly; do NOT touch `ingest.py`; do NOT build incremental.** Reason: resolved history is immutable; re-pulling ~28k rows nightly for ~+16 delta is waste; the eval loop is weekly. Carve-out: paper-trade ENTRY capture is a separate ~30-min `/activity` poll on PINNED whales only (must catch entries while markets are open).
- **B — MIGRATE the roster INTO `prediction_markets.db`** (`pm_roster`/`pm_watchlist` = source of truth; the site owns pin/unpin). Legacy `agent_state` import is a one-time CONVENIENCE SEED (no reads back); post-import drift vs the legacy PCT farm is EXPECTED and FINE (two independent systems until cutover); the site NEVER writes the legacy DB; legacy code may be COPIED (reuse ≠ coupling).
- **C — AUTHELIA owns auth; NO in-app auth, NO `pm_user`/`pm_role`/`pm_grant` tables.** Reason: the pages are identical for Jack/Karen; P2 needs no identity. `pm_user`(Authelia login) ≠ `pm_account`(Kalshi API, P3). Family owner-filtering non-preclusion = a nullable `owner` field on P3's `pm_account` + one sub-division read fn.
- **D — paper trades HOLD TO RESOLUTION; STALE if the whale exits early, VISIBLE + COUNTED** (`n_stale` beside `n_resolved`). Reason: mirroring exits = inventing exit timing (no fill ts) = forbidden by bias-down.

**Build rulings (load-bearing):**
- **(e5) migration-004 caveat columns MUST stay wired into `stats._STATS_COLS` + the rollup SELECT** — `stats.rollup()` does `INSERT OR REPLACE` over `_STATS_COLS`, so ANY caveat column not listed there is reset to DEFAULT (silent-zeroed) on every run, FOREVER. There is a permanent test guarding `set(_STATS_COLS) == pm_category_stats columns`. If Phase 3 touches the rollup, keep it green.
- **(e7) `pm_paper_trade.size_basis` = FIXED CONTRACT/SHARE COUNT**, not dollars — so paper `cost_basis` parallels the external side's `total_bought(NOTIONAL) × avg_price`. Storing dollars would make `cost_basis` mean different things on the two halves of the same scoreboard.
- **(e4) `pm_cli.py` and `rosters.py` ARE editable; only `ingest.py` + the engine files are off-limits.**
- **(e3) ANALYZE (LLM narration, later) = COPY the engine's LLM audit pattern into pm_web + PROVE KeyVault-from-a-2nd-unit FIRST** (don't assume the Anthropic key is reachable).
- **(e6) Only TWO writers ship in P2:** the weekly refresh + the 30-min paper poll. Live marks = P3.
- **prod-live "advance now" (ledger, not gate):** prod-live records WHAT IS ON THE BOX. If artifacts are deployed, prod-live must say so — it is not an approval checkpoint. Drift is silent and grows (this platform already paid down one prod-live-vs-actual reconciliation debt that started exactly that way).

**The three labelling rules — each guards a specific way this data lies (Ruling R4):**
1. **One-sided ROI is rendered LABELED AS AN UPPER BOUND.** Reason: the one-sided slice excludes hedged (two-sided) markets, so it is optimistic *by construction* — a position turns two-sided precisely when the first side sours, so one-sided = the trades that never needed rescue (§13A(f) survivorship). The matcher copies at ENTRY and cannot pick survivors. An unlabeled one-sided ROI is a lie by omission. (Live proof: BetMechanic nba shows one-sided **+33.0%** while overall cost-ROI is **+5.9%** at **71% two-sided** — the gap *is* the caveat.)
2. **`two_sided_pct` carries a GRAIN LABEL: per-`(wallet, category)`.** Reason: the P1 docs' §13A(j) two-sided figures are **per-wallet** and are numerically different — legitimately. Label the column so nobody "corrects" a right per-category number against the per-wallet reference. (Live proof: Kickstand7 reads 17%/20%/46%/26%/20% across mlb/ufc/fed/nfl/nba — not one per-wallet number.)
3. **`single_game_pct` renders `n/a` for `fed` and `unknown`, NEVER `0%`.** Reason: those categories have no single-game notion (`single_game_pct` is NULL by design — OQ-2). A NULL reaching the page as `0%` turns "not applicable" into a finding.

**Ranking framing (§13 dec 11):** the RANKED metric is **cost-based ROI** = `net_realized_pnl / cost_basis` where `cost_basis = SUM(total_bought × avg_price)` (`total_bought` is NOTIONAL = share count, NOT cost). **notional-ROI** (`net/total_bought`) is retained but rendered **muted, "comparison only," NOT ranked** (for legacy/scout comparison). **win%** is displayed but marked a **chalk indicator, NOT the rank key**. Quarantine is clause-(b) only (`pnl_suspect=1`); clause-(a) was DEMOTED to a non-excluding `pnl_anomaly` flag (it false-positived on real MLB losses).

**OQ defaults (accepted, revisit after data):** paper sizing = FIXED UNIT (comparability; the whale's size is a display datum, not a sizing input); paper scores go in a SEPARATE `pm_paper_score_snapshot` (entry-basis ≠ resolution-basis); `PM_ANALYZE_DAILY_USD = $2`.

---

## HARD CONSTRAINTS (non-negotiable)

- **Additive only.** No engine file edits. No engine restart. No legacy DB (`trading_corp.db`) writes. **azureuser is NEVER root** except through the sanctioned root channel.
- **Box-op channels (command-paste-rule — Jack pastes ONE short line `powershell -ep bypass -f .\NAME.ps1`):**
  - **azureuser channel** = the STDIN streamer: `Get-Content -Raw NAME.sh | ssh $h "tr -d '\r\357\273\277' | bash"` (strips CR + the PS-prepended BOM). Pure ASCII, no-BOM `.ps1`, validated with `[scriptblock]::Create`.
  - **root channel** = `az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript` (runs as root; POSIX-safe, LF-only; the deploy runners b64-encode the `.sh` to sidestep quoting).
  - **NO SUDO IS EVER PASTED AT JACK.** A raw `sudo …` for him to paste violates the rule.
- **GOTCHA-2 gate on EVERY deploy:** after any root-context step, the deploy runner must `chown -R azureuser` the PM paths and set modes, with an **acceptance check that FAILS the deploy** if any entry under the PM paths is root/numeric-owned, any **DIRECTORY is 777** (must be 755), or any file is world-writable. Check the DIRECTORIES, not just files.
- **No main merge until cutover.** **prod-live advances for deployed artifacts only** (path-checkout of exactly the deployed files, byte-verified vs the box).
- **⛔ CADDY AND AUTHELIA ARE JACK'S HANDS — READ-ONLY.** You may author config TEXT if asked, and give him the exact edits; you must **NEVER edit or reload** Caddy/Authelia yourself. **This is enforced by a guardrail, not just convention** — the P2 agent's attempt to write Caddy/Authelia *edit runners* was correctly BLOCKED by the classifier (it enforced Jack's earlier read-only boundary against a later instruction). If you hit that block, STOP and report verbatim; do not reword or re-route.
- **⛔ NO DEPLOY until after market close, on Jack's explicit go.**

---

## STANDING PRINCIPLES

- **Bias down, never up.** When a number could be read two ways, render the conservative one; caveats travel WITH the number, adjacent, not in a footnote you can skip.
- **Verify empirically, never narrate.** Paste raw output. If something is unverified, say UNVERIFIED. Prove state on the live box (read-only) rather than asserting it from a doc.
- **Every aggregate drills to its rows** (this IS Phase 3, but it's also the standing test of trust — a number you can't drill to is a number you're asking the user to take on faith).
- **Never reword a blocked command.** If a harness classifier/guardrail blocks something, STOP and report it verbatim — do not reword, re-path, or re-route to slip past it, even if benign.
- **Halt-and-report at forks.** Surface anomalies with diagnostics; don't expand scope; commit artifacts as you go; delegate mechanical work but keep contract-sensitive work yourself.

---

## OPS GOTCHAS (full detail in `reports/prediction_markets/OPS_GOTCHAS.md` — read it; summary here)

- **GOTCHA 1 — root-owned artifacts vs the azureuser runtime.** Every box op runs as ROOT (az = control-plane, not sudo), so root-created files are `root:root` and the azureuser runtime (cron, rollup) can't write them. The first nightly cron died `attempt to write a readonly database` because the DB was root-owned. RULE: prove writes AS azureuser (`runuser -u azureuser …`), own PM artifacts azureuser from creation, `chown azureuser` after any unavoidable root step, never chown broadly, legacy DB off-limits.
- **GOTCHA 2 — box PM-code ownership is a mixed mess** from prior deploy channels (`root:root 666`, `197609:197121` Windows-UID from a tar built on Windows + extracted with `-p`, world-writable dirs). RULE: build artifacts WITHOUT owner metadata (`git archive`; if tarring, `--no-same-owner`), and the deploy runner MUST `chown -R azureuser` + set modes with the FAIL-the-deploy dir-not-777 acceptance check.
- **GOTCHA 3 — the box code lives at the DOUBLE path** `~/trading_corp/trading_corp/prediction_markets` (repo-root layout, `PYTHONPATH=~/trading_corp`; the box tree is NOT a git checkout → deploys are file-copy). RULE: extract with `tar -C ~/trading_corp` so `trading_corp/…` lands at the double path; **prove the target by import-resolution before restart** (`cd ~/trading_corp && venv/bin/python -c "import trading_corp.prediction_markets.stats as s; print(s.__file__)"`). Keep backup-before-overwrite + the sha/gate abort — they caught the wrong-path deploy before harm.
- **Portal + Authelia/Caddy wiring lessons** (full record: memory `[[portal-authelia-caddy-wiring-2026-08-24]]`; you likely can't read agent memory, so the essentials): the Azure Portal Run Command box runs as ROOT, but **scripts copied from a terminal get corrupted** — long lines wrap-break, **heredocs fail** (unmatched delimiter → `cat >>` appends the rest of the script into the target = corruption), **python is whitespace-sensitive** (leading-space → IndentationError). Use ≤72-char lines, **no heredocs, no python** — `echo`/`sed`/`awk` only. And **`authelia config validate` standalone FALSE-POSITIVES** on `jwt_secret` + `storage encryption_key` "required" (runtime secrets injected via the service env, not in `config.yml`) — don't gate on its exit code; use the **service restart + `systemctl is-active`** as the real validator. (These matter only if Jack again asks you to author portal text; you don't run any of it.)

---

## OPEN ITEMS the build must not lose

- **OPEN-A remainder** (GOTCHA-2, partially resolved at CP2 Ph1): the PM-only paths are `azureuser 755/644` and gate-clean, but the **shared `scripts/` dir stays `197609:755`** (engine-interleaved — timer-scheduled `pct-pruner`/`watchlist-stats`/`watchlist-deep`; chowning it would touch engine code), plus the **broader engine-tree ownership mix**. Folded into the standing security item; Jack's-hands, do NOT fix in a checkpoint.
- **Standing security item — WHOLE-PLATFORM, Jack's hands, do NOT fix in a P2/P3 checkpoint** (ordered by what an attacker actually needs):
  - **#1 `0.0.0.0:8000` = the engine dashboard** (python pid child of `trading-corp.service` 850993), remote-reachable IFF a front resource forwards `:8000` + the NSG allows it + `ufw` permits it — all UNVERIFIED from the box (NIC has NO public IP: IMDS `publicIpAddress:""`, private 10.0.0.4; ufw active). **ACTION (Jack, still open): check the NIC's NSG `:8000` inbound rule.** (pm_web binds loopback-only — the correct contrast; it can't drift.)
  - **#2 mixed/phantom ownership** across the engine tree (local, latent priv-esc; needs a foothold first).
  - **#3 world-readable credential-pattern configs = RESOLVED FALSE POSITIVE** — all 8 flagged keys end in `_env` (the value is an env-var NAME, a reference, not an inline secret). Drops to ordinary hygiene: `chmod 640` the ~25 world-readable `.bak/.pre-*` `strategies.yaml` copies during an ownership pass.
- **§13A items a–k** (P1 amendments; some still open): (a) evanng UFC reconciliation; (d) **category ≠ copyability** — a market-type dimension (`market_type_source` seam is in `pm_category_stats`, currently `slug_heuristic`; `gamma_market_type` later) is a P2/P3 concern relevant to drill-through; (f) one-sided survivorship UPPER BOUND (baked into labelling); (h) 57 `cost_basis<=0` rows QUARANTINED (Ruling A); (i)/(j) the two two-sided metrics (per-category vs per-wallet — the grain label); (k) only backfill-complete wallets are ranked. Read `P1_PLAN.md` §13A for the full list + reasons before you touch ranking or the caveat columns.

---

## 🔎 WHAT WENT WRONG, AND HOW IT WAS CAUGHT — the most useful thing in this doc

**Expect the docs you inherit to carry false premises. Verify before you build on them. Build halt-and-report checkpoints where a silent error would otherwise survive.** Concretely, across P1 and P2:

1. **Three false P1-plan premises, caught by verifying instead of trusting:**
   - **"Test locally" (P1_PLAN §10) — there is NO local Python on this Windows dev box** (verified 3 ways). Substitute built: the **box-scratch harness** (git-archive `trading_corp/` + `tests/` + `pyproject.toml`(`asyncio_mode=auto`, or STRICT fails all async tests) to `~/pm_p2_scratch`, box venv, CWD=scratch, `PM_DB_PATH=/tmp` file, per-file sha256 chain-of-custody, delete + prove-gone, engine-PID bracketed). Reuse it (banked at `reports/prediction_markets/runners/pm_p2_phase2_scratch.ps1` + `_p2_probe_phase2.sh`).
   - **"Branch off / cut off prod-live" — WRONG: durable is the base, prod-live had DIVERGED** (the base-vs-deploy-anchor collapse, item below).
   - **P1's implicit "root-created artifacts serve the runtime" — FALSE (GOTCHA 1):** the first cron write died because the DB was root-owned. The premise that "az runs it fine" hid the azureuser-runtime failure until the actual runtime user wrote.
2. **A 27% silent PK data-loss, caught by a single-wallet HALT checkpoint (P1).** The old `pm_closed_position` PK `(wallet, condition_id)` silently collapsed two-sided binary holdings (a whale holding BOTH Yes and No on one market) via `INSERT OR REPLACE` — Kickstand7 dropped 1803→1314 rows. A step-3 single-wallet checkpoint that asserted `pulled == stored` caught it. Fix: migration 002 widened the PK to `(wallet, condition_id, outcome_index)` + an `_assert_no_pk_collision` guard.
3. **The `_STATS_COLS` silent-zero trap, caught BEFORE it existed (CP1 e5).** `rollup()`'s `INSERT OR REPLACE` would reset any migration-004 caveat column not also added to `_STATS_COLS` to DEFAULT on every run — the caveat columns would read ZERO on the product page forever, silently. Caught at design time; a permanent test asserts `_STATS_COLS == table columns`.
4. **The wrong-path deploy, caught by TWO independent safeguards before anything was overwritten (CP2 Ph2).** The first Phase-2 deploy extracted to the single path `~/trading_corp/prediction_markets`. **(a) backup-before-overwrite failed** (`cp: cannot stat …/stats.py` — the real file wasn't there → wrong path), and **(b) the GOTCHA-2 gate tripped** on a stale 777-ish dir → the runner **aborted before restarting pm_web**. Nothing real was overwritten, nothing restarted. Root cause (GOTCHA 3) diagnosed read-only by import-resolution; v2 deployed correctly. **The lesson: keep the backup-before-overwrite AND the acceptance gate — they are cheap and they caught a real mistake.**
5. **The base-vs-deploy-anchor collapse (P2 grounding).** Every governing doc said "cut off the prod-live tip." Topology proved `prod-live` and `durable` had DIVERGED (merge-base `8d77a26`, neither an ancestor of the other): `prod-live` carries the deployed package but NO tests/reports; `durable` carries the P1 test suite + docs. Branching off prod-live would have stranded the build without the test suite. Surfaced; Jack ruled durable = base. **Do NOT trust "cut off prod-live" — confirm the base with Jack.**
6. **The classifier block that enforced a boundary against a later instruction (Caddy/Authelia wiring).** Jack set an early "Caddy/Authelia are my hands, read-only" boundary, then later said "write the edit runners." The guardrail BLOCKED the agent from authoring Caddy/Authelia edit-runners. The right move (which held): report the block verbatim, refuse to reword/re-route, hand Jack the decision. Jack then did every edit himself via the Azure Portal; the agent authored config TEXT only and touched nothing.

**Where Phase-3 halt-and-report checkpoints belong:**
- **Reconciliation checkpoint:** before wiring any drill-through link live, assert (in a box-scratch test) that the link's row count EQUALS the aggregate cell it came from, for every drill (n_resolved, two_sided, single_game, data_quality). A drill that doesn't reconcile is the Phase-3 analogue of the silent PK loss — catch it in the test, not in Jack's browser.
- **Shared-renderer checkpoint:** prove `pm_position_rows.html` renders identically for the product drill-through and a diagnostics call on the same rows (parity, like `scoreboard_flags()` gave CLI/page parity in Phase 2) — so the two consumers can never diverge.
- **Display-name honesty checkpoint:** assert that a whale with NULL `user_name` renders the wallet and never a blank/placeholder-that-reads-as-a-name.
- **Deploy checkpoint (after market close):** the GOTCHA-2 gate + backup-before-overwrite + import-resolution path proof + engine-PID bracket, exactly as CP2 Ph2 did (runner `pm_p2_phase2_deploy.ps1` is banked as the template).

---

## PARITY / PATTERNS FROM PHASE 2 YOU SHOULD REUSE

- **`stats.scoreboard_flags(row)`** is the ONE flag deriver shared by the CLI `format_report` and the web page — that's why CLI/page flag parity is *structural*, not merely tested. Phase 3's drill-through numbers should reconcile with the scoreboard the same structural way.
- **`stats.query_scoreboard`** already LEFT JOINs `pm_category_stats` + `pm_score_snapshot` + `pm_whale` + `pm_category_onesided_stats`. Drill-through queries read the same tables through the same §3A predicate (`db.scoreable_where`) — never re-derive `pnl_suspect = 0` by hand.
- **Off-loop reads:** every pm_web handler runs its DB read via `asyncio.to_thread` on a short-lived `connect()` — keep that (a slow/locked DB must never block pm_web).
- **Honest-empty `—`** everywhere; a real 0 and "no data" are different (Phase 2 guards two_sided% with `n_condition_ids > 0` so an empty slice shows `—`, not `0%`). Do the same for drill-through empties.
- **Assets are VENDORED** (`web/static/htmx.min.js` + hand-authored `web/static/pm.css`) — **NO CDN** on this network-exposed host, **no build step**. Don't reintroduce a CDN. (The engine's own base template uses CDN Tailwind; pm_web deliberately does not.)
- **Templates** live at `trading_corp/prediction_markets/web/templates/` (`pm_base.html`, `pm_macros.html`, `pm_scoreboard.html`, `partials/pm_scoreboard_table.html`). Add `partials/pm_position_rows.html` there.

---

*End of handoff. Build Phase 3, box-scratch it green, HALT before deploy, and wait for Jack's after-close go. Confirm every SHA with Jack, not with this doc.*
