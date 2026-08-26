# CP3b-1 + CP3b-2 — DEPLOY COMPLETE (farm-league UI + on-demand Analyze)

**Deployed + ledgered 2026-08-26.** Two gates, MACE-first, on the live box `tc-prod-vm`.
Additive, read-mostly; zero engine edits; pm_web restarted once; the engine never touched.

## Refs
- **Code tip (deployed):** `bcd505c` on `prediction-markets-cp3b2-2026-08-25`
  (`8661b33` build + `5ed354b` narrate-decouple + `bcd505c` 4 head-bump test-fixes), off cp3b-1 tip `06c00da`.
- **prod-live ledger:** `2fc9173` → **`95e78c4`** (`deploy(pm-cp3b): record CP3b-1+2 farm-league + analyze artifacts on prod-live (== box)`).
  Byte-verified `== box` by a **fresh re-hash at commit time** (not the deploy transcript); prod-live was
  hard-verified `== 2fc9173` before the commit. Fast-forward push, no force. This tip is **load-bearing** —
  MACE's committed ledger forks their next prod-live advance from the post-predictions tip.
- **main:** `2c8aa23` — UNTOUCHED.

## Scope ledgered — exactly 10 `prediction_markets/*` files
Created (5): `analyze.py`, `farm.py`, `web/templates/pm_farm.html`, `web/templates/partials/pm_farm_lists.html`,
`web/templates/partials/pm_analyze_result.html`.
Modified (5): `db.py`, `web/app.py`, `web/static/pm.css`, `web/templates/pm_base.html` (nav-link only),
`web/templates/pm_macros.html` (append-only).
**Excluded on purpose:** `scripts/pm_cli.py` (Q3 ruling — keeps the whole deploy out of `scripts/`/OPEN-A;
the analyze subcommand ships later if wanted), the package `__init__` files (unchanged, already on box),
tests, and docs. **`persistence/db.py` confirmed ABSENT from the artifact** (enumerated + name-guard STOP +
`^trading_corp/prediction_markets/` leak-abort) — it is MACE's shared file, to which they added an additive
`available_buying_power` column the same night; a similarly-named different file is exactly how a wrong
artifact ships, so absence was proven, not assumed.

## Gate 1 — migration 007 → live PM DB (2026-08-26 ~03:25Z; azureuser channel, no code landed)
Applied via the **tested `init_db()` from an ephemeral scratch extract** (byte-verified `== bcd505c`); the box's
runtime `db.py` was not touched until Gate 2. Online backup taken first (kept).
- schema **6 → 7**; `pm_analysis_cache` + `pm_analysis_cost` created and **both empty** (columns verified).
- `pm_closed_position` **29,709 → 29,741** — the 03:20 UTC refresh cron **survived the Bastion reboot and fired**
  (`20 3 * * * … pm_cli.py refresh --cap 50000`), ingesting 32 new resolutions. `pm_paper_trade` open 102;
  `pm_roster`/`pm_watchlist` 114/114.
- pm_web **PID 642 NOT restarted**; `/healthz` returned schema **7 on CP2-Phase-3 code** — the honest Q5
  behavior (healthz reads the DB, not the code), now observed. Engine PID 37596 unchanged; legacy mtime unchanged.

## Gate 2 — code deploy + pm_web restart (2026-08-26 ~03:44Z; root az vm run-command channel)
Fail-closed: manifest-assert (10 PM-only) → chain-of-custody (box==local) → GOTCHA-3 double-path import proof →
per-file backup → GOTCHA-1/2 chown-azureuser gate (dirs 755 / files 644 / no world-writable) → restart pm_web only.
- **10 files deployed**, custody + gate clean. `DEPLOY_VERDICT=OK`.
- pm_web **642 → 40483** (restarted); engine **37596 unchanged**; `/healthz` **200, schema 7**; legacy mtime unchanged.

### Live verification (report-only, against the running site)
- **Regression:** scoreboard `/` renders (known name), a whale page returns 200, and "Farm league" is a live nav
  tab — the shared `pm_base.html`/`pm_macros.html` changes did not break the live pages.
- **Farm page (live):** **114 pinned** across **18 category tabs**; three-state **0 never-polled / 105
  polled-nothing-open / 9 with-open**; **1** quarantined-baseline pair; **11** unknown pairs; **0 candidates**
  with the true "No search has run yet" message. Matches the box-scratch prediction exactly. The legend states
  *"never polled = not yet OBSERVED (absence of observation, not of activity)"* in words on the page (amendment G
  where a reader sees it); `unknown` renders as a category with a tier-1-miss tag; caveats travel (↑bound on every
  one-sided ROI, CHALK/CONTESTED inline, an em dash where avg-win-px is genuinely absent).
- **Analyze live on the 3 pairs** (real POSTs to the running pm_web): BetMechanic/nba → `llm_unavailable`;
  SDTrading/fifwc → `llm_unavailable`; 4751346 (`0x6dd6…`)/nfl → `no_resolved_positions` (2 rows, both
  quarantined → 0 scoreable). Deterministic grid on all three; cache/cost **still 0** (nulls don't spend).
  Every `llm_unavailable` is correct and expected — the key is not wired (see open items).

## Journal — both windows CLEAN (closed the Gate 1 gap)
On Gate 2's root channel, with **epoch** timestamps:
- Gate 1 migration window (03:25:00–03:25:10 UTC): **no SQLITE_BUSY / locked**.
- Gate 2 restart window: **no busy / lock / traceback**.

**Why the Gate 1 grep hadn't run:** the Gate 1 runner passed `journalctl --since/--until` an ISO timestamp with a
`T` separator, which journalctl rejects ("Failed to parse timestamp"). That is a **timestamp-format bug in the
runner, NOT a box-permissions constraint** — the runner mislabeled it "journal NOT readable as azureuser." The
label was corrected before it could become a fact someone acts on; the corrected epoch grep on the root channel
confirmed both windows clean. Recorded so no future work re-derives a nonexistent permissions limit.

## Scoreboard nav WARN — resolved as a CHECK ARTIFACT
Gate 2's `[10a]` printed `NAV_FARM_LINK missing`. Run down locally *before* asserting anything: `pm_scoreboard.html`
extends `pm_base.html`; the deployed `pm_base.html` carries `href="/farm"` (custody-verified byte-identical); and
the exact grep pattern matches the exact rendered markup in a local test. The live page was **not** fetched
(look-first flow). Jack confirmed in-browser that "Farm league" IS a live nav tab. **The check was wrong, not the
page.** The `href="/farm"` nav check in the Gate 2 runner is therefore **unreliable and must not be re-raised as a
fresh finding** by a future run.

## Backups still on the box (rollback material; keep until stale)
- **Gate 1 DB backup:** `/home/azureuser/pm_cp3b2_gate1_dbbackup_20260826T032502Z.db` (schema 6 / 29,741 rows, sha `fe89bde2…`).
- **Gate 2 per-file dir:** `/home/azureuser/pm_cp3b2_gate2_bak_20260826_034428/` (the 5 pre-overwrite MODIFIED files).
- Gate 1 rollback: `DROP TABLE pm_analysis_cache; DROP TABLE pm_analysis_cost; DELETE FROM schema_version WHERE version=7;`
  (additive, zero data loss) or restore the DB backup.
- Gate 2 rollback: restore the 5 files from the per-file dir, delete the 5 created files, restart pm_web
  (schema-7 DB inert to reverted CP2-Phase-3 code).

## Engine PID history (so a future reader does not misread it as drift)
`969439` (through CP3a) → `1020085` (after the MACE restart) → `673` (after the Bastion VM reboot) →
**`37596`** (now; MACE deploy restart, board-confirmed). Bracket future box runs against **37596**; pm_web is
now **40483**.

## Open items — UNCHANGED by this deploy
- **e3 / KV wiring UNPROVEN.** The Anthropic key is not reachable from pm_web, so every Analyze verdict renders
  `llm_unavailable` — correct and expected, not a defect. Wiring is `KEY_VAULT_URI` on the pm_web unit +
  a forked KV pull + a live "KeyVault-from-2nd-unit" proof; **an importable langchain/azure library is capability,
  not a working token.** Jack's hands, a separate third decision. Not in this deploy.
- **Adjudicator chain** still needs a poller re-run (nothing adjudicable until a poller pass flips open →
  pending_adjudication). The 03:20 cron is live and current (29,741) — the first link is healthy — but the
  adjudicator itself is not part of this work.
- **cap-at-100** pagination fix, **Ruling-B** refresh-source flip — both still CP3b-later.
- Two **record-only CP2 items** (NSG 8000 defence-in-depth; engine-tree mixed ownership) unchanged.

## Record-only observation — thin data in the LIST view (for a later decision, NOT tonight)
The live farm/scoreboard list surfaces something the specs never did: `000why000` alone spans 11 categories,
several at n=1 or n=2 — e.g. `epl` n=2 at −15.7%, `mlb` n=1, `ucl` n=1 at −6.3%. Those percentages are
arithmetically correct and statistically meaningless, and they render in the same column with the same formatting
as a +90.5% on n=477. CONTESTED catches some of it. This is the **thin-data question already ruled for Analyze
verdicts, appearing in the LIST view where it was not ruled.** Whether the list should mark thinness the way
Analyze does is a real, open question — Jack's, later. **Do not act on this.**

## Not done tonight (explicitly)
No KV wiring. No CP3b-3 Search. No adjudicator run. No poller re-run. No cap-at-100. No Ruling-B flip.
No engine restart. Nothing MACE deployed or the 13-file box-ahead state was touched. `poly_kalshi_mlb` untouched.
