# TRANSITION → P2 BUILD AGENT (Prediction Markets)

## ⭑ PICK UP HERE (read this first)

- **STATUS: P2 plan is BOARD-APPROVED FINAL (2026-08-23, Jack). Execution NOT started. Nothing built, nothing deployed, nothing mutated.** The spec is `reports/prediction_markets/P2_PLAN.md` on THIS branch (`prediction-markets`).
- **YOUR FIRST ACT, IN ORDER:**
  (a) Read this doc in full, then `P2_PLAN.md`, then `PLATFORM_VISION.md` and `P1_PLAN.md` **including all amendments** (§3A, §7, §12, §13 decisions, §13A open items).
  (b) **Confirm the CURRENT `prod-live` tip WITH JACK before branching** — the P1 build was nearly cut off a stale SHA, and remembered numbers go stale the same way. **NEVER a remembered SHA.**
  (c) Cut `prediction-markets-p2-2026-08-23` off the confirmed tip.
  (d) Push to origin from the first commit; work phase-branch → durable `prediction-markets`; **no `main` merge** until the P3 cutover.
- **ALREADY DONE — facts on the branch, do NOT re-derive:** P1 is deployed + live (**daily 03:20 cron TODAY; Ruling A replaces it with a WEEKLY schedule at build**; ~28.3k rows, 12 whales); the read-only **infra discovery** (Caddy proxy; Authelia `forward_auth` on :9091; PM port **`127.0.0.1:8081` verified free**; `predictions` DNS record absent; systemd house style — §4); the **four board rulings + OQ rulings** (§3). These are settled; build on them.
- **EXPLICITLY OUT OF SCOPE:** the **Authelia edit** (Jack's — `AUTHELIA_TRADING_RULE_FINDING.md`); **Karen's login** (gated on that edit); anything **P3** (promote-to-live, sub-divisions, the shared execution engine, live marks); the **backlogged analytics** (BetMechanic `/activity` entry-timestamp study, the two-sided directional slice as a one-off probe — if they happen they become APP COLUMNS, not probes). Do NOT edit Caddy/Authelia. Do NOT touch `ingest.py`. Do NOT create auth tables.
- **BUILD CHECKPOINTS YOU MUST HONOR (carry P1's hardest lesson — §9):** expect this plan to contain wrong premises too; verify against live data before building on any of them; build **halt-and-report checkpoints** into the P2 sequence at these four points, inspecting the real rows before fanning out:
  1. **first migration (004) applied to the LIVE P1 DB** — confirm idempotent + existing rows intact before proceeding;
  2. **first page rendering REAL data** — confirm the caveat columns + drill-throughs match the CLI (`format_report`) on real rows;
  3. **first search run** — one category, inspect `pm_search_run` + the found candidates + the targeted backfill BEFORE allowing bulk;
  4. **first paper trade captured** — one pinned whale, inspect the `pm_paper_trade` row (entry_ts, side, size) BEFORE the poll runs across the farm.
- **HARD CONSTRAINTS + STANDING PRINCIPLES** are in §6 and §7 — read them; they are non-negotiable (additive only, no engine edits/restart, no legacy DB writes, no main merge, `.ps1` runner pattern, azureuser-never-root; bias-down-never-up, verify-don't-trust, caveats-travel-with-the-number, drill-to-rows, never-reword-a-blocked-command).

---

**Written 2026-08-23. Assume you (the build agent) have read nothing else.** This is your complete handoff.
The detailed spec is `reports/prediction_markets/P2_PLAN.md` on this branch (`prediction-markets`), reachable from
origin and the box — read it after this. Everything a decision rests on is here *with its reason*; a decision
without its reason gets relitigated, so do not skip the reasons.

**Scope of P2:** a **separate website** for the Prediction Markets platform (farm-league product + diagnostics),
reading/writing only `data/prediction_markets.db`, no engine coupling. You own everything up to but **not
including** promote-to-live (that's P3). Build nothing live-money.

---

## 1. What P1 delivered + current LIVE state (as of 2026-08-23)

P1 is deployed and live. It is a **read-only ingestion pipeline + offline scoreboard** — it trades nothing.

- **Package:** `trading_corp/prediction_markets/` = `db.py` (separate SQLite, WAL, numbered idempotent
  migrations, legacy-isolation guard `_assert_not_legacy`), `category.py` (two-tier: tier-1 slug-prefix →
  tier-2 gamma `/events` tag-join), `ingest.py` (backfill/refresh, `_pull_closed` w/ 429 backoff + cap +
  completeness flag, the §3A quarantine helpers, `_assert_no_pk_collision`), `rosters.py` (reads legacy
  `agent_state` read-only), `stats.py` (`rollup` → `pm_category_stats`, two ranking routines, `query_scoreboard`,
  `format_report`). CLI `trading_corp/scripts/pm_cli.py` (g0-validate/backfill/refresh/rollup/repair-categories/report; flags `--cap`, `--only-wallets`, `--dry-run`, `--format json`).
- **DB `data/prediction_markets.db`** on tc-prod-vm: **~28.3k closed rows across 12 whales** (2 live MLB —
  SDTrading, xifutloong3 — + 10 PCT-farm paper whales incl the UFC/NFL/NBA/Fed scouts). Cost-based ROI on two
  routines (`net_roi`, `recency_weighted`). Separate from legacy `data/trading_corp.db` (never contended).
- **Current refresh cadence = DAILY cron `20 3 * * *` (03:20 UTC)** in azureuser crontab, `refresh --cap 50000`.
  **★ P2 REPLACES THIS WITH A WEEKLY schedule (Ruling A, §3 below).** The daily cron's first fire failed on a
  file-ownership bug (root-owned DB vs azureuser cron) — **now FIXED**: the DB is `azureuser:azureuser`, refresh
  proven as azureuser (12/12 complete, pulled==stored, rollup updated_ts advanced). See `OPS_GOTCHAS.md`.
- **Git:** durable integration branch **`prediction-markets`** (this branch) carries the whole record as docs +
  the package. phase branch `prediction-markets-p1` @ `f88ffa5`. **`prod-live`** @ `86fb433` carries the deployed
  artifacts only. **`main` UNTOUCHED @ `2c8aa23`** (no PM code on main until the P3 cutover).
- **Docs on this branch** (`reports/prediction_markets/`): `PLATFORM_VISION.md` (locked vision), `P1_PLAN.md`
  (executed plan + §13 decisions + §13A open items), `P2_PLAN.md` (your spec), `DEPLOY_COMPLETE.md`,
  `OPS_GOTCHAS.md`, `FARM_RERANK_2026-08-23.md`, `POSTP1_ITEMS_2-4_2026-08-23.md`, `EVENTS_TAG_SCHEMA.md`,
  `REALIZEDPNL_PROBE_RESULT.md`, `QUARANTINE_RECONCILE_2026-08-22.md`, `NET_VERIFY_TARGET.md`, `STEP5_REPORT.md`,
  + `runners/` (the read-only + deploy `.ps1`/`.py` runners).

---

## 2. The P2 shape (see P2_PLAN.md for the page-by-page + DDL)

Separate `pm_web` FastAPI process under `trading_corp/prediction_markets/web/`, standalone (no `WebDeps`, no
engine imports), Jinja2 + HTMX + Tailwind (no build step), reusing the engine web idioms (the `mace_view`
off-loop honest-empty read pattern; the poly_kalshi `hx-trigger="load, every Ns"` polling). Product tabs:
scoreboard, farm league (category sub-tabs, each WATCHLIST + PINNED-PAPER list), whale detail, search. Plus a
diagnostics tab. New migrations **004–007** add: caveat analytics (two_sided_pct, single_game_pct, one-sided
directional slice), paper trading (`pm_paper_trade` + `pm_paper_category_stats` + `pm_paper_score_snapshot`),
roster/watchlist/search (`pm_roster`/`pm_watchlist`/`pm_search_run`), and `pm_analysis_cache`. Background jobs:
weekly full refresh, a ~30-min paper-entry capture, and an in-process search worker. **No auth tables** (Authelia
owns identity — §3 Ruling C).

---

## 3. The four board rulings of 2026-08-23 (WITH REASONS — do not relitigate)

- **RULING A — WEEKLY full refresh; do NOT build incremental refresh; do NOT touch `ingest.py`.**
  *Reason:* resolved position history is IMMUTABLE — re-pulling ~28k settled rows nightly to catch a handful of
  new resolutions is waste (a same-day re-run delta was +16 rows), and the scoreboard's purpose is the WEEKLY
  evaluation loop (review farm, decide pins/promotions), which nothing about last night's resolutions serves.
  Weekly, a full pull is fine even at 40+ whales (~1 h, once a week, colliding with nothing) — so incremental
  refresh (which only existed to defend a *nightly* window against roster growth) is unnecessary. Replace the
  daily 03:20 cron with a weekly one (P2_PLAN §7.1 proposes **Sunday 09:00 UTC** — re-verify the slot live at
  install, never a remembered map). **CARVE-OUT:** paper-trade ENTRY capture is a *separate, faster* job — you
  must catch a pinned whale's entry while the market is open, via `/activity` polling on PINNED WHALES ONLY (not
  `/closed-positions` re-pull). ~30-min cadence. Different job, different source (§7.2).
- **RULING B — MIGRATE the roster INTO `prediction_markets.db`** (`pm_roster`/`pm_watchlist` source of truth;
  the site owns pin/unpin/watchlist writes). *Reason:* the separate-site isolation story (Ruling C / P1 §3) means
  the site must not depend on the legacy DB at runtime; `rosters.py`'s legacy `agent_state` read is replaced by a
  PM-native roster. The one-time import from legacy `agent_state` is a **CONVENIENCE SEED, not a link** — NO reads
  back after it. **Post-import drift between the legacy PCT farm and the PM farm is EXPECTED AND FINE** (two
  independent systems until cutover; nothing is meant to stay in sync). The site **NEVER writes the legacy DB**.
  Legacy code may be **copied/reused** to expedite (reuse ≠ coupling). **Documented second-order effect (don't
  "fix"):** the two paper farms accrue SEPARATE records from the import forward — at cutover there are two partial
  paper records per whale, not one continuous one. Expected; don't be surprised the new farm shows less history.
- **RULING C — AUTHELIA owns auth (already exists); NO in-app auth, NO `pm_user`/`pm_role`/`pm_grant` tables.**
  *Reason:* `trading.jacksumner.com` is already behind Authelia; `predictions.jacksumner.com` goes behind the same
  instance with per-domain rules, enforced at the proxy BEFORE the request reaches `pm_web` — the strongest
  boundary, matching the separate-site rationale. A parallel in-app user table would be a second place defining
  "who sees what" = worse. `pm_user` (Authelia login) ≠ `pm_account` (Kalshi API, a P3 attribute); the mapping
  between them is the future access model. **P2 needs no user identity** (pages identical for Jack & Karen; no
  per-account filtering yet). **See §5 for the DESIGN-AFFECTING discovery finding you must not lose.**
- **RULING D — paper trades HOLD TO RESOLUTION; STALE if the whale exits early, VISIBLE + COUNTED.** *Reason:* the
  live poly_kalshi division already ruled hold-to-resolution the GOOD case (whale conviction), not a limitation;
  mirroring exits means modeling exit timing from data with no fill timestamps = inventing numbers, which the
  bias-down principle forbids. **HARD REQUIREMENT:** `n_stale` sits beside `n_resolved` ON THE PRODUCT PAGE — an
  exclusion you can't see is survivorship you can't audit (the same trap as the one-sided-slice finding).

### OQ rulings (the finer decisions)
- **OQ-1 paper sizing = FIXED UNIT.** *Reason:* fixed sizing makes whales comparable; mirroring imports the
  whale's bankroll into the signal. The whale's own bet size is a DATA POINT TO DISPLAY, not a sizing input.
  Per-sub-division sizing = P3.
- **OQ-5 paper scores = SEPARATE `pm_paper_score_snapshot`** (not `pm_score_snapshot` with a tag). *Reason:*
  paper = entry-basis + forward-only; external = resolution-basis + historical; one table invites the conflation
  P1 fought.
- **OQ-6 `PM_ANALYZE_DAILY_USD` = $2/day** (hard-skip narration, audit-only, reasoned-null on breach). *Reason:*
  Jack just took Anthropic spend from ~$300/mo to ~$60/mo; $5/day (~$150/mo) would exceed the whole platform
  spend. $2/day ≈ $60/mo ceiling.
- **OQ-2/3/4/8/9 = accepted defaults, REVISIT AFTER FIRST REAL DATA** (same call Jack made on the clip bounds):
  single_game_pct NULL for Fed; paper-poll 30 min; weekly slot Sun 09:00 UTC (mandatory live re-verify at
  install); pinned-paper recency defaults to entry_ts / external to resolved_ts (labeled); one `pm_roster` row per
  (wallet, category).

---

## 4. Infrastructure facts (discovered read-only 2026-08-23; raw in P2_PLAN §11/§12)

- **Reverse proxy = Caddy** (`/etc/caddy/Caddyfile`; admin `127.0.0.1:2019`; binds `:80`/`:443`, automatic HTTPS).
- **Authelia** on `127.0.0.1:9091`, wired via Caddy `forward_auth localhost:9091 { uri /api/authz/forward-auth;
  copy_headers Remote-User Remote-Groups Remote-Name Remote-Email }`. This is the exact block the `predictions`
  vhost copies. Session cookie scoped to apex `jacksumner.com` (SSO across sub-domains).
- **PM web port:** bind **`127.0.0.1:8081`** (verified free; loopback-only so PM is reachable ONLY via
  Caddy+Authelia — unlike the engine dashboard on `0.0.0.0:8000`). In use: 22, 53, 80, 443, 2019, 8000, 9091.
- **DNS/TLS:** `predictions.jacksumner.com` has **NO DNS record** (`trading` → `172.171.189.116`). Adding it =
  A record → 172.171.189.116; a Caddy site block → `reverse_proxy localhost:8081`; Caddy auto-provisions the
  cert (no `tls` directive = automatic per-site HTTPS, no SAN mgmt).
- **systemd house style** (match it for `prediction-markets-web.service`): `Type=simple`, `User=azureuser`,
  `Group=azureuser`, `WorkingDirectory=/home/azureuser/trading_corp`, `Restart=on-failure`+`RestartSec=10`+
  `StartLimit*`, `StandardOutput/Error=journal`, hardening (`NoNewPrivileges`, `ProtectSystem=strict`,
  `PrivateTmp`, `ReadWritePaths=…/data …/logs`), env via `KEY_VAULT_URI` (Anthropic key for ANALYZE) +
  `PYTHONUNBUFFERED`. **No `xvfb-run`** (that's engine-only, for the headless browser).

---

## 5. ⚠ THE LINCHPIN — a DESIGN-AFFECTING auth finding you must NOT lose or paper over

Authelia's `access_control` today:
```
default_policy: deny
rules:
  - domain: trading.jacksumner.com
    policy: two_factor          # <-- NO subject/user/group restriction
```
Users DB = file backend (`/etc/authelia/users_database.yml`), **single user `jack` [admins]**.

**The `trading` rule has no `subject`** → *any* authenticated 2FA user reaches trading. Safe today only because
`jack` is the sole user. **The moment Karen gets an Authelia login (required to see `predictions`), she also
satisfies the unrestricted `trading` rule → she could reach the LIVE TRADING dashboard.** So Ruling C ("Karen
sees predictions, not trading") **cannot be delivered by only adding a `predictions` rule.**

**This is a TRADING CORP INFRA CHANGE (same category as the VM geo-migration) — NOT a P2 build task. Jack's
hands, Jack's timing. You (the build agent) do NOT touch Caddy or Authelia — you build `pm_web` + its systemd
unit + the migrations/UI only.** The full ordered procedure (and the safety steps — take a config rollback copy,
work from a machine with a separate way back in, and **VERIFY JACK STILL REACHES TRADING after tightening the
rule** before creating Karen, because a wrong `subject` on a `default_policy: deny` config locks Jack out of his
own live dashboard) is in the standalone note **`AUTHELIA_TRADING_RULE_FINDING.md`**. Ordering in brief:
(1) add `subject` to the trading rule → (2) verify Jack still reaches trading → (3) create Karen → (4) add the
`predictions` rule → (5) DNS A-record + Caddy site block.

**★ THIS DOES NOT BLOCK THE P2 BUILD.** Authelia's existing `two_factor` rule already covers a new `predictions`
vhost — Jack reaches it, nobody unauthenticated does — so the site can be built, deployed, and used by Jack long
before Karen's login exists. **What the finding blocks is the Karen viewer feature, NOT the build.** Do not treat
auth as a build gate; do not attempt the config edit. If anything about the build surfaces that the auth ruling
still can't be delivered cleanly, say so plainly — do not paper over it.

---

## 6. HARD CONSTRAINTS (non-negotiable)

- **Additive only.** No edits to engine files. No engine restart. `pm_web` is a separate unit; its restart must
  not touch `trading-corp.service`.
- **No legacy DB writes.** The site touches only `prediction_markets.db`. `rosters.py`'s legacy read is replaced
  by `pm_roster` (Ruling B); the one-time legacy import is read-only + one-shot.
- **No `main` merge until the single P3 cutover.** Work on `prediction-markets-p2-<date>` → durable
  `prediction-markets`. Push early/continuously.
- **All box operations via the `.ps1` runner-file pattern** (`command-paste-rule`): box ops live in a `.ps1`
  FILE invoked `powershell -ep bypass -f .\NAME.ps1`; the classifier only sees the one-line invocation. NEVER
  inline box bash (rm/mv/globs) in the tool command; NEVER base64-tunnel a denied action. Pure ASCII, no BOM,
  parse-validated before running.
- **Run as azureuser, NEVER root (OPS_GOTCHAS GOTCHA 1).** Every box op runs via `az vm run-command` = ROOT, so
  any file it creates is root-owned — but the runtime (cron, `pm_web`) is **azureuser**. Prove writes as the
  runtime user (`runuser -u azureuser -- …`), own artifacts azureuser from creation, or `chown
  azureuser:azureuser` the specific artifact after an unavoidable root step. Never chown broadly; legacy DB
  off-limits. (This is how P1's first cron fire failed — root-owned DB vs azureuser cron.)
- **Do NOT touch `ingest.py`** (Ruling A). Do NOT create auth tables (Ruling C).
- **Do NOT edit Caddy/Authelia** (§5 — Jack's hands).

---

## 7. STANDING PRINCIPLES that shaped this build (carry them)

- **BIAS DOWN, NEVER UP.** Every ambiguity resolves conservatively. A whale that looks worse than it is costs a
  missed opportunity; one that looks better costs money. (P1 produced three upward-bias failures, all caught.)
- **Verify empirically; never trust narration or a doc.** Three P1 plan premises were FALSE (see §9). Each was
  caught only by checking live data. Assume your plan has false premises too.
- **Caveats travel WITH the number.** two-sided %, single-game %, avg_win_price (chalk/contested), $-weighted
  data_quality, `n_stale`, the one-sided-slice UPPER-BOUND label — all on the PRODUCT page, not hidden in
  diagnostics. A number without its caveat lies (BetMechanic +$1.12M next to "71% two-sided" tells the truth).
- **Every aggregate drills through to its rows.** Click any stat → see the positions behind it. Nearly every P1
  defect was found this way. Make drill-through first-class.
- **Read cost-ROI + avg_win_price + the one-sided slice — NEVER the composite score alone** (it over-credits
  chalk at edge≈0: AIisTheNewWD scored 0.967 on 99% win with +0.2% ROI).
- **Do NOT reword a blocked command.** If a classifier/guardrail blocks a command, STOP and report verbatim —
  never re-path/reword/base64 to slip past it. Let Jack decide (he may run it himself). (This happened twice in
  planning; both were procedural and Jack cleared them.)

---

## 8. OPEN ITEMS the build must not lose (P1_PLAN §13A a–k)

- **(a) evanng UFC — RESOLVED positive** 2026-08-23 (+$12,068 / +24.0% cost-ROI closed-positions); paper-added.
- **(b) Fed / negRisk realizedPnl decoupling** — quarantined by §3A; surface `data_quality` where politics/negRisk appear; do not feature contaminated pairs.
- **(c) gamma enrichment edge cases** — tier-2 `/events` tag-join available; ~85–90% covered by tier-1.
- **(d) MARKET-TYPE ≠ CATEGORY** — a category blends single-game moneylines with futures/props (different skills).
  Reliable discriminator = `sportsMarketType=='moneyline'` + `gameStartTime`, market-level, NOT in
  `/closed-positions`. **P2 ships the slug-heuristic `single_game_pct` now; true gamma `market_type` deferred**
  (a `market_type_source` seam is reserved). 4751346 UFC is only ~44% single-fight — surface the mix.
- **(e) politics unreliable** under this data source — surface where it appears; don't rank it prominently.
- **(f) survivorship in the one-sided slice** — the one-sided cost-ROI is an UPPER BOUND (a position turns
  two-sided when the first side sours). Label it; the matcher copies at entry and can't pick survivors.
- **(g) BetMechanic** — one-sided +33% but 71% two-sided; do NOT promote to live until an `/activity`
  entry-timestamp study; keep on paper. (P3 concern; don't lose it.)
- **(h) cost_basis ≤ 0** rows quarantined (`no_cost_basis`) — inherited; they don't reach the scoreboard.
- **(i) composite score over-credits chalk** — see §7 (read cost-ROI + avg_win_price).
- **(j) two-sided ≠ directional** — high two-sided = weak copy signal (matcher is fail-closed first-side-wins).
  Display two-sided %. Material: BetMechanic 71%, FordBronco 70%, Kickstand7 46%, 4751346 41%, Kh4mz4t 38%.
- **(k) ownership gotcha** — see §6 (azureuser, GOTCHA 1).

---

## 9. WHAT WENT WRONG IN P1 AND HOW IT WAS CAUGHT (expect the same; build the same checkpoints)

Three plan PREMISES turned out FALSE — each caught ONLY because something was verified against live data instead
of accepted from the doc:
1. **"gamma tags live on `/markets`"** → FALSE; they live on `/events`. Caught by a live tag probe (the tier-2
   join was built against `/events` as a result). If it had been trusted, categorization would have silently
   returned empty tags.
2. **"Fed is universally clean"** → FALSE; Fed is whale-dependent (Kickstand7 had 3 suspect Fed rows incl a dust
   leg propagating to winners; pako 0). Caught by running the actual §3A predicate on live rows. The record was
   corrected.
3. **"realizedPnl is direct per position"** → FALSE for negRisk winner-take-all (event-level decoupling; the
   −$17M d1k21 artifact). Caught by the realizedPnl probe → drove the entire §3A quarantine design.

And the one that would have poisoned the whole scoreboard:
- **The PK collapse — 27% SILENT data loss.** The original PK `(wallet, condition_id)` `INSERT OR REPLACE`-
  collapsed two-sided binary holdings (Kickstand7: 1803 pulled → 1314 stored). **Caught by a SINGLE-WALLET
  DEPLOY CHECKPOINT** — Kickstand7 was backfilled alone and inspected BEFORE the other eleven wallets landed. The
  fix (PK += outcome_index, migration 002, + `_assert_no_pk_collision` guard) shipped before any bad data spread.

**Instruction to you:** expect your P2 plan to have false premises too. Before you fan a mutation across the farm
(a migration backfill, a bulk paper-open, a search-driven bulk backfill), run it on ONE unit first and inspect
the rows — the single-wallet checkpoint is why P1's worst bug cost nothing. Verify against live data; distrust the
doc, including this one.

---

## 10. Branch model + first step

- Confirm the **CURRENT `prod-live` tip with Jack before branching** — NEVER a remembered SHA (P1 §4). Then cut
  `prediction-markets-p2-<date>` off durable `prediction-markets` (or the confirmed base Jack names), push to
  origin from the first commit.
- Phase branch → durable `prediction-markets`. **No `main` merge** until the single P3 cutover. `prod-live`
  advances for deployed artifacts only (P2_PLAN §12).
- `P2_PLAN.md` (this branch) is **BOARD-APPROVED FINAL (2026-08-23, Jack)** — build from it. Execution not started.
- Migrations are **004–007** (auth tables removed per Ruling C). Follow `db.py`'s numbered/idempotent pattern
  exactly; never rebuild a P1 table.

**First move:** read `P2_PLAN.md` in full, then re-verify the live state yourself (the cron, the DB row counts,
the box layout) rather than trusting this doc's numbers — per §9.
