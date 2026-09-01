# PM TRANSITION — SESSION WRAP 5 (2026-08-31, ~22:15Z)

> ⛔ SUPERSEDED 2026-09-01 by `TRANSITION_SESSIONWRAP6_2026-09-01.md` — READ THAT FIRST. Since SW5, the ENGINE BUNDLE
> (opposed-memory + shard-snapshot writer + migration 016) and the pm_web BATCH (M3-display + M4 scoping/gates +
> PM_ADMIN_IDENTITIES=jack + Karen's owner_identity) were DEPLOYED LIVE. The multi-account phase is deployed, not
> just built; engine PID is now 144229. SW5's live numbers below are the 2026-08-31 snapshot and are stale.

> ★★★ THIS SUPERSEDES `TRANSITION_SESSIONWRAP4_2026-08-31.md`. Read THIS first. SW4 predates the OPPOSING-SIDE GUARD
> deploy, the caps raise to 50/$150/$150, and the count_ceiling latch+clear. Everything below is observed read-only
> at ~22:15Z. NO polling monitor is running — the next agent discovers state by READING, not by inheriting a watch.

═══════════════════════════════════════════════════════════════════════════════
## ★ 0. JACK-MLB IS ARMED AND TRADING LIVE — do NOT disarm (intended)
- **effective_armed=True, latched=False.** Observed via `arm.read_arm_verdict`. 3 whales (SDTrading 0x16bb +
  xifutloong3 0x2dc1 + 0x684baa57), **5 contracts/order, 50 orders/day, daily_usd_cap $150, max_open_usd $150**,
  3 market types. Account kalshi_jack / mlb.
- **★ STOP command, verbatim:** `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`
- **orders_today = 22 / 50** (28 headroom). **21 open positions, 110 contracts.** max_id=24 (R8 placed id 23 NYY-LAA
  + id 24 SDCIN-spread after the 21:49 re-arm — trading resumed at the new cap).
- **NO OPPOSED close has fired (0 rows)** — the guard is correctly SKIPPING a 4th opposition (`0x2308d78d`) every
  ~8s cycle, not closing. **3 pre-existing pairs still held both sides, none settled yet** (games in progress).
- engine **127578** (NRestarts 0; restarted 21:33 for the opposing-guard deploy), pm_web **124014** (NRestarts 0),
  schema **15**. boot_reconcile last verdict reconciled=True latched=False **@21:37:14** (engine unrestarted since).
  shard-3 **$455.25** (falling as R8 buys; shard-0 $0.008).

## ★ 1. FIRST ACTION — the settlement walk, once tonight's games finish (before ANY build)
Run **`cc\pm_settlement_walk_ro.ps1`**. It answers THREE things, all load-bearing:
1. **The per-settlement walk INDIVIDUALLY** (not a total) — a dozen-plus closes booked unattended by a path with ONE
   hand-inspected case (the Cubs); the four named risks answered ONE AT A TIME (two settling same 600s window / a
   settle-while-Option-D-evaluates double-close / a partial-or-zero fill / realized vs the WRONG entry price at N=5).
   Also: boot_reconcile still CLEAN after; NO NO-leg.
2. **★ THE SHARD-PROCEEDS QUESTION — now a DEPENDENCY, not a curiosity.** $150/day (the new daily cap) against
   shard-3 **~$455** is roughly **THREE DAYS** if proceeds SWEEP to shard 0 instead of returning to shard 3 — Karen's
   silent death (a healthy TOTAL while the FUNDING shard empties). If they sweep: the caps need revisiting
   IMMEDIATELY and a shard top-up becomes a DAILY operational task. Say plainly: **return-to-3 or sweep-to-0**.
3. **★ THE THREE OPPOSING PAIRS' LOCKED LOSSES, totalled as ONE NUMBER** — the measured cost of the requirements
   miss. Pairs: BALCOL (0x521f47a9), SDCIN moneyline (0x1bac2543), MIAWSH (0xc38041b8). Total them.
   **★ CORRECTED 2026-09-01 (settlement walk, `SETTLEMENT_WALK_2026-09-01.md`):** the "~8.7c" above was mis-attributed
   to BALCOL — **8.7c is MIAWSH's** realized (MIA -2.5935 / WSH +2.5065 = -0.0870). **SDCIN moneyline = -0.1345 (13.5c)**
   (SD +2.0080 / CIN -2.1425). **Two settled = -0.2215 total (22.2c); BALCOL still OPEN** (2040 game, both legs +5 held).
   The real cost is HIGHER than the single-pair estimate implied — which strengthens the guard, not weakens it.

## ★ 2. JACK'S OUTSTANDING TASK — the KEY_VAULT_URI unit line (Analyze narration)
Until it lands, Analyze prints the deterministic table + loss-completeness block and writes NO sentence (STATE 1:
narration unavailable, pm_web healthy). `skill_version` is FINAL at **3**, so the key goes whenever Jack wants and the
first PAID narration is final-form. Exact command (az-root; mirrors the engine unit + daemon-reload + restart pm_web,
NO engine touch):
```
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts \
  "grep -q 'KEY_VAULT_URI' /etc/systemd/system/prediction-markets-web.service || sed -i '/^Environment=PM_WEB_PORT=8081/a Environment=\"KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/\"' /etc/systemd/system/prediction-markets-web.service; systemctl daemon-reload; systemctl restart prediction-markets-web"
```
It is a SCOPED Anthropic-only fetch (least privilege — pm_web holds no trading keys), fail-soft. After it,
`cc\pm_r2c_postcheck_ro.ps1` should show STATE 2 (`narration ENABLED`).

═══════════════════════════════════════════════════════════════════════════════
## 3. WHAT SHIPPED TODAY (all LIVE; branch `pm-optiond-whale-exit-2026-08-31` @ `f8e61a5`, local==origin; prod-live NOT advanced)
Option D whale-EXIT copy · R-d settlement-CLOSE · Stage 5 loss-grounding → the Analyze page · the prompt rung
(skill_version 3) · the scoped least-privilege KV fetch · the /live SETTLED display fix · **and the OPPOSING-SIDE GUARD.**

## ★ 4. THE OPPOSING-SIDE RULE — Jack's FINAL wording (+ both wrong readings, recorded so they are not repeated)
**When two whales take OPPOSITE sides of the same market, the bet comes OFF THE BOOKS: CLOSE what we hold, SKIP the
incoming, end FLAT.** Keyed on `(condition_id, outcome_index)` — opposing = same cid, DIFFERENT oidx (resolves the leg
question uniformly across moneyline/total/spread; a different LINE is a different cid and never opposes).
- **✗ NOT "ignore the second signal"** — that was Jack's LEGACY quick fix, explicitly NOT the requirement.
- **✗ NOT "close then take the new side"** — that would leave us net on whichever whale signaled later (arbitrary).
- **✓ THE CONVERSE IS THE DESIGN: same side, multiple whales → COPY EACH.** 10 whales on one side = 50 contracts —
  agreement is conviction. (The earlier "per-market cap gap" framing was STRUCK.)
- **The flatten is PER-WALLET** (one close per holding whale, summing to the full account flatten) — the review
  rejected an account-net-under-one-wallet approach that double-booked the per-wallet settlement scan.
- **PRE-EXISTING pairs are LEFT ALONE** — the guard PREVENTS new pairs, it does not retroactively flatten (else it
  would have closed 3 games at once on boot; Jack ruled let BALCOL settle).

## ★ 5. THE MEASURE OF THE MISS + the guard live
**THREE opposing pairs formed in a few hours with three whales** — BALCOL + SDCIN + MIAWSH — all while the guard was
being built. Deployed 21:33Z, it left all three to settle and is preventing a 4th (`0x2308d78d`) within minutes.
Their locked losses (§1.3) are the measured cost. This number, not the single BALCOL pair, is what the miss cost.

## ★ 6. FOUR STANDING LENSES ADDED TODAY (do not re-litigate)
- **RETROACTIVE ENFORCEMENT** ([[retroactive-enforcement]]) — a new guard must act on the EVENTS it observes, not on
  STATE it INHERITS; else it enforces retroactively against pre-existing state, overriding a prior human decision.
- **A WRITE MUST SATISFY EVERY VIEW** ([[a-write-must-satisfy-every-view]]) — state aggregated by >1 key (ticker AND
  (cid,oidx)) needs a terminal write to carry ALL keys, or one view silently diverges (the settlement NULL-cid phantom).
- **A DEPLOY MANIFEST IS THE IMPORT CLOSURE, NOT THE DIFF** ([[deploy-manifest-is-import-closure]]) — bit twice
  (R7.e boot_reconcile, R2c loss_grounding); Gate-A over transitive imports catches it pre-restart.
- **A GREP IS A TEXT MATCH, NOT A STATE CHECK** ([[grep-is-not-a-state-check]]) — verify state from the system that
  holds it (systemctl show / /proc / a hash / the DB), never from text that might merely mention it.
- Plus the standing: a safety check that fails open ([[safety-check-silently-stops-checking]]); the NO-leg lens.

## ★ 7. WHALE-PROPORTIONAL (mode 3) — verdict + price-bucket follow-on
`reports/prediction_markets/WHALE_PROPORTIONAL_FINDINGS_2026-08-31.md`. The first run POOLED all 18 categories (Jack
caught it); re-run PER category. **NO category supports building mode 3** (return-per-dollar is the verdict metric):
contraindicated in unknown/atp/fifwc/cs2/ucl; not-justified in mlb/nba/soccer/wta/nhl/wnba/cbb/tennis; **no-signal in
ufc/nfl/epl**; insufficient in golf/fed. Win-rate edge is a CHALK artefact everywhere. Also recorded: Claude's UFC
mechanism guess was wrong (dispersion AMPLIFIES the confound, not dilutes). **★ The price-bucket cut is the study's
real output** — return-per-dollar falls monotonically with entry price (the edge is in PRICE/longshots, not size) —
**BUT it's the most F-1-contaminated number** (losing longshots→$0=the omitted losses), an UPPER BOUND gated on
Stage-5 `loss_grounding` re-grounded per (whale, category, price-bucket). That is the follow-on worth doing.

## ★ 8. NEXT SESSION'S AGENDA — after the walk: MULTI-ACCOUNT
A different KIND of work (UI + data modelling). Karen as a second `pm_account` row; per-account pages with **P&L and
win/loss across sub-divisions**; permission scoping via `owner_identity` + Authelia (first use of both); **arm/disarm
in the UI** with the CLI staying the authoritative kill path; **shard-aware balance display**.
- **★ Jack's rulings (do NOT re-derive):** Karen IS the second account and CAN be added, but **DO NOT attach a whale**
  — LEGACY (poly_kalshi_mlb) still trades that Karen account, and a PM attachment would confuse open-position tracking
  across the two systems. **Karen gets a login** (no concerns; Authelia + owner_identity exist, never used).
- **★ THE UI REWRITE WAITS BEHIND MULTI-ACCOUNT, deliberately** — the account layer changes navigation and what a
  page MEANS; designing first means redesigning after. **Two interim additions Jack wants meanwhile:** (a)
  plain-language MARKET DESCRIPTIONS instead of raw tickers; (b) realized **P&L per position**.
- Then: the price-bucket re-grounding follow-on (§7); shard money-mgmt; account-pages backlog.

## ★ 9. LOCK-IN ARBITRAGE (backlog) — its TENSION with the opposing-side rule
A lock-in arb DELIBERATELY holds BOTH sides of one market when the prices sum < $1 (a guaranteed profit). That is the
EXACT opposite of the opposing-guard, which flattens both sides. They cannot both be live on the same path unarbitrated:
the guard is about COPYING two DISAGREEING whales (no edge → off the books); an arb is US taking both sides ON PURPOSE
(positive edge → hold both). If a lock-in arb is ever built it needs its OWN path, EXEMPT from the opposing-guard (or
the guard must distinguish "two whales disagree" from "we are arbing"). Do not build one without resolving this.

## ★ 10. HOUSEKEEPING — today's artifacts (KEEP/REMOVE + restore-impact NOW THAT THE DIVISION TRADES AT 50/$150/$150; nothing deleted)
★★ **DANGEROUS — a DB restore silently reverts caps/schema/journal on a LIVE ARMED division; DISARM + reconcile first:**
- `~/pm_r5_caps_backup_20260831T214753Z.db` (21:47) — **the pre-caps-write PM DB.** Restore NOW → **silently reverts
  50/$150/$150 back to 20/$60/$60** (R8 would re-latch at 20 within the UTC day) AND loses every journal row since
  21:47 (ids 23-24 + tonight's settlements). ★★ The caps-critical one. **KEEP**, never restore blind.
- `~/pm_rd_deploy_backup_20260831T160923Z.db` (16:09) — Gate-1 **pre-schema-15** PM DB. Restore → schema 15→14
  **UNBOOKS every settlement** (R-d terminal-close rows gone) + loses all today's journal (ids 2-24) + reverts caps.
  ★★ Different class: it unbooks settlements. **KEEP.**
- `~/pm_mig014_backup_...020430Z.db` (02:04, pre-14), `~/pm_caps_write_backup_...022238Z.db` (02:22, pre-old-caps),
  `~/pm_reattach_backup_...022934Z.db` (02:29) — pre-today's-trading PM DB snapshots; a restore loses the whole day's
  journal + caps + settlements. **KEEP** (catastrophic-rollback points), never restore blind.

**CODE/FILE backups (no DB/caps impact; a restore reverts CODE and needs a restart to take effect):**
- `~/pm_r5_deploy_backup_20260831T211517Z/` (21:15) — the 3 pre-opposing-guard ENGINE files. The R5 rollback
  (removes the guard; engine restart). **KEEP** until the guard is settled (working — safe to remove in a day).
- `~/pm_r2c_deploy_backup_20260831T185617Z/` (18:56) — pre-R2c pm_web files. R2c rollback. **KEEP** short-term.
- `~/pm_rd_deploy_files_backup_20260831T161414Z/` (16:14) — pre-Option-D/R-d ENGINE files (removing settlement.py
  breaks the settlement path). **KEEP** (deep rollback), DANGEROUS-adjacent, needs a restart.
- `~/pm_r2c_deploy_backup_20260831T185330Z/` (18:53) — FAILED first R2c attempt, IDENTICAL base to 185617Z.
  **REMOVE (redundant)** — recommend, do not delete now.
- `~/pm_rulingA_backup_...011834Z/` (01:18), `~/pm_sizing_code_backup_...020713Z/` (02:07) — early code backups,
  superseded. **REMOVE when convenient** (low value), not dangerous.
- /tmp scratch + the deploy tars are auto-cleaned by the runners.

## 11. STATE SUMMARY (observed 22:15Z)
effective_armed=True/latched=False · caps 50/$150/$150 (per_order $5.50, contracts 5, slippage 2, liquidity_ratio
0.75) · engine 127578 (NR 0) · pm_web 124014 (NR 0) · schema 15 · orders_today 22/50 · 21 open / 110 contracts · 0
OPPOSED closes · 3 pre-existing pairs unsettled · boot_reconcile reconciled=True latched=False @21:37:14 · shard-3
$455.25 · the four PM crons (`*/30 paper-poll`, `05:00 refresh`, `05:40 adjudicate`, `05:50 rollup`) · branch
`f8e61a5` local==origin · prod-live NOT advanced.

## 12. OPERATING RULES / BOX QUIRKS / SETTLED RULINGS (not to re-open)
- **command-paste-rule** ([[command-paste-rule]]): box cmds via Jack-authorized `.ps1` runners in `cc\` holding
  pure-ASCII `.sh`, STDIN-piped (`Get-Content -Raw $sh | ssh $h "tr -d '\r\357\273\277' | bash"`); validate parse +
  0 non-ASCII; **restarts are az-root via `Desktop\restart_tc.ps1` (engine, bitunix bounce) / `restart_pmweb.ps1`.**
- PM pkg NESTED at `/home/azureuser/trading_corp/trading_corp/prediction_markets/`; live PM DB
  `/home/azureuser/trading_corp/data/prediction_markets.db` (WAL); pm_web loopback 127.0.0.1:8081 behind Authelia
  (curl loopback bypasses Authelia); shared venv; arm state in the LEGACY DB `agent_state` (via read_arm_verdict, NOT
  the PM DB); the driver re-reads `sub_config_from_row` PER CYCLE (a caps write is live next cycle, NO restart).
- Settled rulings: opposing rule = FLAT/close-held/skip-incoming (§4); same-side = copy each; pre-existing left to
  settle; per-wallet closes; scoped fetch NOT load_secrets; skill_version 3; mode-3 NOT built (per category); Karen
  add-yes/attach-no; caps 50/$150/$150 (count = runaway breaker, dollars = bankroll control, bind on different failures).
- STOP conditions standing: a NO-leg fill · a latch → report don't clear (a count_ceiling latch is the DESIGNED
  breaker, clearing it is an acknowledgement AFTER raising the cap) · engine PID moving on a pm_web deploy · /live
  losing the open positions · an OPPOSED close firing on a same-side copy (the guard must never).
