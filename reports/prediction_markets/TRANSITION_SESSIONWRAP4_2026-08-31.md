# PM TRANSITION — SESSION WRAP 4 (2026-08-31, ~19:10Z)

> ★★★ THIS SUPERSEDES `TRANSITION_SESSIONWRAP3_2026-08-31.md`. Read THIS first. SW3's live state (schema 14,
> pre-Option-D/R-d, pre-Stage-5) is stale. Everything below is observed read-only at ~19:07–19:10Z.

Next agent: multi-account (UI + data modelling) is next, a different KIND of work than order-path safety. But
**tomorrow opens with the settlement walk** — read this record; do not rebuild the thing it checks.

═══════════════════════════════════════════════════════════════════════════════
## ★ 0. JACK-MLB IS ARMED AND TRADING LIVE — do NOT disarm (intended overnight)
- **ARMED, latched=False, effective_armed=True.** Proven behaviourally: 13 real placements today, R8 still placing
  through the deploy window (id=15 at ~18:20Z). Arm/latch state persists in the LEGACY DB (scope-keyed,
  `arm._scope_latched_failsafe(legacy_db_path)`), read authoritatively by `pm_cli` — that is why it is NOT a PM-DB table.
- **Config:** 3 whales (SDTrading 0x16bb + xifutloong3 0x2dc1 + 0x684baa57), **5 contracts/order, 20 orders/day**,
  3 market types (moneyline/total/spread). Account kalshi_jack / mlb.
- **★ STOP command, verbatim:** `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`
- **Orders: 15 total** (dry_run=0) — 14 entries (is_exit=0) + 1 settlement-close (id=8 Cubs). max_id=15.
- **Open positions: 12 tickers, 65 contracts, all YES** (journal-derived net): 11 tickers @ 5ct + SEA-BOS-Aug31 @ 10ct
  (two entries same ticker). The Cubs is NOT held (settled, netted flat). ★ Still **NO NO-leg has ever filled** — if
  an Under/away-spread fills, STOP + hand-inspect (the −NO reconcile sign is still inference-only).
- **PIDs:** engine **119559** (NRestarts 0, UNTOUCHED all session), pm_web **124014** (NRestarts 0, restarted this
  session for the R2c deploy — was 119393). Schema **15**.
- **boot_reconcile last verdict:** reconciled=True latched=False @16:33:29Z (engine has not restarted since, so this
  stands). Tonight's PM settlement scan (600s) has booked NOTHING yet — the 12 positions settle later tonight.

## ★ 1. THE SETTLEMENT WALK — after tonight's games settle, before build work resumes (NOT "tomorrow")
★ FRAMING (Jack, 2026-08-31): games are RUNNING now and settle TONIGHT — Jack is working through today, so this is
NOT "tomorrow's first action" and the doc must not imply a stop. Run **`cc\pm_settlement_walk_ro.ps1`** once tonight's
games have settled, whenever that is, BEFORE any build work resumes. This is R-d's SECOND unattended settlement cycle
— up to 12 positions settle tonight (SD/CIN, DET/MIN, ATH/TEX, PHI/AZ, MIA/WSH, SEA/BOS, + Sept-1 games + the totals),
each booked automatically by a path that has hand-processed exactly ONE position (the Cubs).
- **PER-SETTLEMENT WALK, not a total.** For each: the venue settlement record (market_result/revenue/settled_time)
  vs the R-d booked row (is_exit=1, close_source='settlement', fill_count, won, realized_pnl, settled_ts); recompute
  `realized = net_open*settled_value − net_open*avg_cost` by hand for at least the winners (N=5 was never exercised —
  Cubs was N=1).
- **The 4 named risks answered INDIVIDUALLY** (not "no anomalies"): (1) two settling in the same 600s window — each
  its own row, no cross-contamination; (2) a settle-while-Option-D-evaluates double-close — exactly ONE terminal
  close per ticker, net-open→0 once; (3) a partial/zero-fill — books ACTUAL net-open, a no_fill never gets a close;
  (4) realized vs the WRONG entry price at N=5. Also: boot_reconcile still CLEAN after the scans; NO NO-leg.
- **★ THE SHARD-PROCEEDS ANSWER** — open since Karen's division died silently on a healthy-looking TOTAL balance.
  Baseline was shard-3 **$495.19** (16:35Z); it is now **$478.89** (R8 bought entries down; shard-0 $0.0081). After
  settlements: does a WIN's ~$5/pos credit RETURN to shard 3 (self-sustains) or SWEEP to shard 0 (depletes → needs a
  target allocation)? Say PLAINLY which. **If every position loses, the question stays OPEN — a legitimate answer,
  not a failure.** A winner answers it.

## ★ 2. JACK'S OUTSTANDING TASK — the KEY_VAULT_URI unit line (Analyze narration)
Until it lands, Analyze prints the deterministic table + loss-completeness block and writes NO sentence (STATE 1 now:
`pm_web: KEY_VAULT_URI unset … narration stays unavailable`, pm_web healthy). `skill_version` is FINAL at **3**, so
the key can go whenever Jack wants and the first PAID narration will be final-form. Exact command (az-root; adds the
line the engine unit already has, then daemon-reload + restart pm_web — NO engine touch):
```
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts \
  "grep -q 'KEY_VAULT_URI' /etc/systemd/system/prediction-markets-web.service || sed -i '/^Environment=PM_WEB_PORT=8081/a Environment=\"KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/\"' /etc/systemd/system/prediction-markets-web.service; systemctl daemon-reload; systemctl restart prediction-markets-web"
```
After it, `cc\pm_r2c_postcheck_ro.ps1` should classify **STATE 2** (`… narration ENABLED`). It is the SCOPED
Anthropic-only fetch (least privilege — pm_web holds no trading keys), fail-soft (a broken vault → STATE 3
`… failed … boots normally`, distinct from STATE 1's `unset`).

═══════════════════════════════════════════════════════════════════════════════
## 3. WHAT SHIPPED TODAY (all LIVE; branch `pm-optiond-whale-exit-2026-08-31` @ `41e02e1`, local==origin; prod-live NOT advanced)
- **Option D — whale-EXIT copy** (order path): follow a whale OUT via /activity SELL + /positions reduction; reduce_only
  exit priced off the BID (marketable IOC); per-wallet net-open guard prevents double-close. 3-reviewer adversarial pass.
- **R-d — settlement-CLOSE** (Kalshi's own settlement as authority): books a terminal close (is_exit=1,
  close_source='settlement', realized_pnl) with no order; boot settlement-scan + 600s periodic. Deployed with Option D
  16:33Z (schema 14→15, mig-015). The Cubs (id=8, −$0.6084) is its one hand-verified case.
- **Stage 5 loss-grounding → the Analyze page** (this session's R2b/R2c/prompt rung): re-grounds a whale's LOSS set
  from /activity (the F-1 held-to-worthless omission), shows honest W/L + omission % + completeness bound BESIDE the
  stats; the grounded set now flows INTO the narrator prompt (new top caveat tier); `skill_version` 2→3.
- **Scoped least-privilege KV fetch** in `scripts/pm_web.py` — DELIBERATELY not load_secrets() (Jack ruled); fail-soft;
  comment- + test-guarded against a future "simplify to load_secrets()".
- **/live SETTLED display fix** — a settlement-close renders **SETTLED** (won/lost) with realized P&L, distinct from a
  whale **EXIT**; the Cubs row was mislabelled EXIT with a $0.00 fill (the fix Jack spotted). Post-check confirmed live.
- All 7 pm_web files hash-gated + Gate-A green; pm_web restarted (124014); STATE 1 confirmed; Cubs reads SETTLED
  −0.6084; loss-block renders on BetMechanic/nba (byte-offset asserted beside the stats).

## ★ 4. WHALE-PROPORTIONAL (mode 3) VERDICT — PER CATEGORY + correction history
`reports/prediction_markets/WHALE_PROPORTIONAL_FINDINGS_2026-08-31.md` (runner `cc\pm_whale_proportional_bycat_ro.ps1`).
- **★ Correction history:** the FIRST run POOLED all 18 categories (grouped by wallet, no category filter — ~22k of
  120,542 is uncategorized 'unknown'). Jack caught it: sizing is per (account,category), so pooling is the wrong grain.
  Re-run per (whale, category). **Also recorded: Claude's UFC mechanism GUESS was wrong** — reasoned wider dispersion
  would WEAKEN the chalk confound; the data says UFC has the LARGEST confound (+0.127 vs MLB +0.040). Trust the
  per-category data over the mechanism story.
- **Verdict metric = RETURN PER DOLLAR** (sizing scales money, not frequency). **No category supports building mode 3.**
  Contraindicated (p<0.05): unknown/atp/fifwc/cs2/ucl. Not-justified (weak): **mlb −0.077 p=0.068 (LIVE)** /nba/soccer/
  wta/nhl/wnba/cbb/tennis. No-signal (delta~0): **ufc −0.003 p=0.827 (NEXT go-live)** /nfl/epl. Insufficient: golf(3
  whales)/fed(2). **The win-rate edge is universal AND a chalk artefact everywhere** (large group always higher entry
  price). UFC is the one live-relevant category without a per-dollar penalty — but NO-SIGNAL, not endorsement; re-run
  when UFC has more history. Keep flat `contracts` sizing.

## ★ 5. THE PRICE-BUCKET HYPOTHESIS — the study's real output AND its most contaminated number (the follow-on worth doing)
In EVERY category, return-per-dollar **falls monotonically with entry price** — longshots pay +1.6..+2.6/dollar,
favorites ~0. The edge is in PRICE (longshots), not bet size. **BUT it is the most F-1-contaminated number, in the
direction that inflates it:** a losing longshot resolves to $0 = a held-to-worthless loss = exactly what
/closed-positions drops. So the low buckets are missing their losers → an UPPER BOUND. **Stage 5's `loss_grounding`
is the tool to re-ground it per (whale, category, price-bucket).** That is the follow-on worth doing — do not act on
the raw longshot edge; re-ground first. It makes Stage-5 loss-grounding the prerequisite for the one idea in the study.

## ★ 5b. OPPOSING-PAIR GUARD MISSING — the requirement, its FIRST MEASURED COST, and the rulings (2026-08-31)
★ CORRECTION (Jack): SAME-side stacking is CORRECT behaviour (agreement = conviction; 10 whales on one side = 50ct,
intended) — the earlier "per-market cap gap" framing is STRUCK. The real requirement: **when two whales take OPPOSITE
sides of the same market, the bet comes off the books — we CLOSE the position we hold.** Not "ignore the second
signal" (that was legacy's quick fix), not "hold both" (guaranteed loss of the spread+fees).
- **★ FIRST MEASURED COST OF THE MISSING GUARD: 8.7¢, 2026-08-31.** We held BOTH sides of `KXMLBGAME-26AUG312040BALCOL`
  (Orioles/Rockies): BAL (oidx=0, whale 0x684baa57) + COL (oidx=1, SDTrading), both YES 5ct, same Polymarket cid
  `0x521f47a9`. Prices summed to exactly $1.00 so the locked loss = the two fees (~$0.087). Jack RULED: **LET IT
  SETTLE** (closing = 2 more spreads+fees into a pre-game book to recover <9¢ — very likely worse; outcome is
  determined either way). One opposing pair out of 17 tickers with 3 whales attached — THIS number is the argument
  for the build.
- **The leg question is SOLVED by (condition_id, outcome_index):** opposing = SAME cid, DIFFERENT oidx (BALCOL cid
  shared, oidx 0 vs 1); same-side = same cid+oidx (SEABOS); different line = DIFFERENT cid (SDCIN total-9 `0x3f88`
  vs total-10 `0x1d60` — NOT opposing). No Kalshi ticker-string parsing; works for moneyline/total/spread uniformly
  (each is one Polymarket binary market with mutually-exclusive outcomes).
- **★ RULINGS for the build (Jack 2026-08-31):** (#2) FLAT — close the held side, SKIP the incoming (neither side
  held; signal ordering carries no information). (multi-copy) flatten ALL of it — 3 whales' 15ct closes fully, not
  "one whale's worth" (the per-wallet accounting everywhere else makes one-wallet the easy accidental bug — TEST it).
  Close via the SHARED terminal-close primitive (not a third path); `close_source='opposed'` with its own /live label
  beside SETTLED/EXIT; exit-exempt on budget gates but DISARM STILL BLOCKS; incoming gets a labelled SKIP not an error;
  guard must NEVER fire on a legitimate same-side copy. (Build in progress this session; review; halt for deploy.)
Investigated read-only 2026-08-31 (runner `cc\pm_seabos_dump_ro.ps1` + `cc\pm_seabos_venue_ro.ps1`). SEABOS-SEA (Aug31)
showed **10 contracts** = TWO DIFFERENT whales copying the SAME side: id=10 wallet `0x684baa57` (signal_id fb54635d,
coid e16526f9, 17:17:31Z) + id=12 wallet `0x16bb9951`/SDTrading (signal_id dfcab59d, coid 151fa144, 18:54:50Z), both
YES @ 0.40, same condition_id `0x3706bf52…` / outcome_index 0 (same entry key `pos:0x3706bf52…:0`). **Venue confirms:
`position_fp="10.00"`, journal net +10 — they MATCH.**
- **NOT a dedup defect.** The Finding-5 same-whale re-entry key is not implicated: signal_id (execution.py:202), the
  idempotency coid (:441) and the holding-guard `journal_net_open_contracts` (:646 `AND wallet=?`) are ALL per-wallet,
  so two different wallets are two independent copies — gate 4 CORRECTLY allowed both. No same-whale re-entry occurred.
- **★ THE GAP: nothing caps PER-MARKET exposure.** The in-memory caps (execution.py:290-332) are: `per_order_usd_cap`
  (per order), `daily_usd` + `orders_today` (per account+category), `open_usd` (per ACCOUNT, total across all markets).
  **None is per-market/per-ticker.** So a single game carries N_whales × 5 contracts — with 3 whales attached, up to
  **15 contracts (3× the intended single-whale size)** on one market, bounded only by the $60 account open cap + 20
  orders/day. This is correct copy behaviour (independent signals) but **Jack should know a single game can already
  reach 3× size before he adds more whales.** If a per-market cap is wanted, it is a NEW gate (per (account, category,
  condition_id) exposure), not a config of the existing caps. Filed to the backlog; not a stop, not a defect.

## ★ 6. NEXT SESSION'S AGENDA
**Settlement walk FIRST (§1), then MULTI-ACCOUNT** — a different KIND of work (UI + data modelling):
- Karen as a second `pm_account` row; per-account pages with **P&L and win/loss across sub-divisions**; permission
  scoping via `owner_identity` + Authelia (first-ever use of both); **arm/disarm in the UI** with the CLI staying the
  authoritative kill path; **shard-aware balance display** (the total masks the split — Karen's silent-death class).
- **★ Jack's rulings, given (do NOT re-derive):** Karen IS the second account and CAN be added, but **DO NOT attach a
  whale to it** — LEGACY (poly_kalshi_mlb) still trades that same Karen account, and a PM attachment would confuse
  open-position tracking across the two systems. **Karen gets a login today** (no concerns; Authelia + owner_identity
  exist for it, never used).
- **★ The UI REWRITE WAITS BEHIND multi-account, deliberately** — the account layer changes navigation and what a page
  MEANS, so designing first means redesigning after. **Two small interim additions Jack wants meanwhile:**
  (a) plain-language MARKET DESCRIPTIONS instead of raw tickers; (b) realized **P&L per position**.
- Then: the price-bucket re-grounding follow-on (§5), the shard money-mgmt backlog, account pages backlog.

## ★ 7. HOUSEKEEPING — today's artifacts (KEEP/REMOVE + restore-impact NOW THAT THE DIVISION TRADES; nothing deleted)
★★ **DANGEROUS — a restore reverts settlement bookings / caps / today's journal on a LIVE ARMED division; DISARM +
full reconcile first, never restore blind:**
- `~/pm_rd_deploy_backup_20260831T160923Z.db` (16:09Z, ~106MB, the Gate-1 PM DB) — **pre-schema-15, pre-today's
  trading.** Restore NOW → reverts schema 15→14, ERASES the Cubs settlement (id=8) AND all of today's R8 journal
  (ids 2–15) while the venue still holds the 12 positions → journal-vs-venue divergence, boot_reconcile latches. **KEEP.**
- `~/pm_rd_deploy_files_backup_20260831T161414Z/` (16:14Z, engine files) — **pre-Option-D/R-d code.** Restore NOW →
  removes settlement.py + reverts the whale-exit/settlement-close engine on a division whose tonight's settlement scan
  depends on it. Needs DISARM + engine restart. **KEEP.**
- `~/pm_mig014_backup_20260831T020430Z.db` (02:04Z, pre-schema-14), `~/pm_caps_write_backup_20260831T022238Z.db`
  (02:22Z, pre-caps), `~/pm_reattach_backup_20260831T022934Z.db` (02:29Z), `~/pm_caps_set_backup_20260830T180415Z.db`
  (Aug30, pre-caps) — all **pre-today's-trading DB snapshots**; a restore loses today's journal + caps. **KEEP** (valid
  catastrophic-rollback points), never restore blind on a trading division.

**SAFE / operational (pm_web-only or superseded):**
- `~/pm_r2c_deploy_backup_20260831T185617Z/` (18:56Z) — the R2c rollback (6 pre-R2c pm_web files). pm_web-only, no
  engine/DB/order impact; to roll back R2c also `rm loss_grounding.py` + restart pm_web. **KEEP** until R2c is settled
  (post-check green — safe to remove in a day).
- `~/pm_r2c_deploy_backup_20260831T185330Z/` (18:53Z) — the FAILED first-attempt backup, IDENTICAL base to 185617Z.
  **REMOVE (redundant duplicate)** — recommend, do not delete now.
- `~/pm_rulingA_backup_...011834Z/`, `~/pm_sizing_code_backup_...020713Z/` (early today, file backups from RulingA +
  flat-contracts sizing) — superseded by later deploys; **REMOVE when convenient** (low value), not dangerous.
- /tmp: stale `pm_*.json/.txt/.html` + orphaned `.db-shm/.db-wal` sidecars (Aug 27–29) — harmless, cleared on reboot.
  The R2c deploy tar is already cleaned. **REMOVE (or leave for reboot).**

## 8. STATE SUMMARY (observed 19:07–19:10Z)
- arm ARMED/latched=False/effective=True · engine PID 119559 (NR 0) · pm_web PID 124014 (NR 0) · schema 15 ·
  orders 15 (14 entry + 1 settlement) · 12 open positions / 65 contracts all YES · boot_reconcile reconciled=True
  latched=False @16:33:29Z · shard-3 $478.89 / shard-0 $0.0081 · branch `41e02e1` local==origin · prod-live NOT advanced.
- **The four PM crons:** `*/30 paper-poll` (the poll boundary), `05:00 refresh --cap 50000`, `05:40 paper-adjudicate`,
  `05:50 paper-rollup` (the 05:00–05:50 window). Time any pm_web deploy CLEAR of these. (+2 non-PM crons; systemd timers:
  pct-pruner, watchlist-stats, pm-watchlist-deep, watchlist-deep.)

## ★ 9. STANDING LENSES (do not re-litigate; today added two)
- **A DEPLOY MANIFEST IS THE IMPORT CLOSURE, NOT THE DIFF** ([[deploy-manifest-is-import-closure]]) — NEW, confirmed
  recurrence (R7.e boot_reconcile + today's R2c loss_grounding). A modified file that gains a new import drags a new
  dependency the diff omits; Gate-A over transitive imports catches it pre-restart.
- **A GREP IS A TEXT MATCH, NOT A STATE CHECK** ([[grep-is-not-a-state-check]]) — NEW. Verify state from the system
  that holds it (systemctl show / /proc / a hash / the DB), never from text that might merely mention it (the
  KEY_VAULT_URI "OMITTED" comment false-positive).
- A SAFETY CHECK THAT FAILS OPEN ([[safety-check-silently-stops-checking]]); the NO-leg inversion lens; hash-as-the-gate;
  a green suite that never runs the real path; a fixture must mirror the real row.

## 10. OPERATING RULES / BOX QUIRKS / SETTLED RULINGS (not to re-open)
- **command-paste-rule** ([[command-paste-rule]]): box cmds via Jack-authorized `.ps1` runners in `cc\` holding
  pure-ASCII `.sh`, STDIN-piped (`Get-Content -Raw $sh | ssh $h "tr -d '\r\357\273\277' | bash"`); validate parse +
  0 non-ASCII; **restarts are az-root via Jack's `Desktop\restart_pmweb.ps1` / `restart_tc.ps1`**.
- PM pkg is NESTED: `/home/azureuser/trading_corp/trading_corp/prediction_markets/`; live PM DB
  `/home/azureuser/trading_corp/data/prediction_markets.db` (WAL); pm_web loopback 127.0.0.1:8081 behind Authelia
  (curl loopback bypasses Authelia); venv `/home/azureuser/trading_corp/venv/bin/python`; the shared venv HAS
  azure-identity/azure-keyvault-secrets (the scoped fetch's libs).
- Settled rulings: Option D forks B1(full-close net-open per-wallet)/A1(in-memory)/C3(defer Finding-5); scoped fetch
  NOT load_secrets; skill_version final at 3; mode-3 NOT built (per category); Karen add-yes/attach-no.
- STOP conditions standing: a NO-leg fill · a latch → report don't clear · engine PID moving on a pm_web deploy ·
  /live losing the open positions.
