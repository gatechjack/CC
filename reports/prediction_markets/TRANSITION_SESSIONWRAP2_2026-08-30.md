# PREDICTION MARKETS — TRANSITION TO NEXT AGENT (2026-08-30, FINAL)

> **★ THIS SUPERSEDES `TRANSITION_SESSIONWRAP_2026-08-30.md` — read THIS first.** That doc retains the fuller
> narrative of the Stage-4 / pm_cli / tile-fix deploys and the prod-live advance (its §1-§8); this one carries the
> live state, the sharding finding (now RESOLVED), the open design question, what the next agent is for, and the
> R7.f gate as it now stands. Observed state stamped **2026-08-30T16:51Z**. **Nothing is armed; no order has ever
> been placed on the money path.**

---

## 0. STATE — four of six stages live

- **Stages 0-3 + Stage 4 deployed; Stage 4 RUN** — 134 prospects across all 15 allowlist categories; the Farm
  League is populated for the first time. **The tile-vanish fix is live** (category exists by allowlist, not by
  pinned rows). Branch `prediction-markets-stage3-r55-2026-08-29` @ the tip of this doc's commit; **origin/prod-live
  @ `e5fbc60`** (advanced this session, FF-only, records the three deploys; R7/MACE/PEAD explicitly NOT in it).
- **Order path: nothing armed, no order ever placed.** `pm_subdivision_order=0`, arm DISARMED (0 `pm_live` rows),
  engine `trading-corp` PID **76416** / pm_web **89704** (both NRestarts 0), schema **13**, sub-division
  `kalshi_jack/mlb`.
- **The legacy `poly_kalshi_mlb` copy division is HEALTHY and copying again** (see §1) — it is a SEPARATE division
  on Karen's account, not PM's money path.

---

## 1. ★ THE SHARDING FINDING — the most important operational fact this platform has learned

**Kalshi shards collateral across exchanges** — `exchange_index` 0 Default · 1 Combos · 2 Crypto · **3 Tennis &
Baseball**. **Orders auto-route to the market's shard** (when `exchange_index` is omitted, as our order body does),
and Kalshi charges **that shard's** balance — **NOT the total.** **The total-balance figure MASKS the per-shard
split, and our reader (`bal.balance / 100`, brokers/kalshi_live.py:278) returns exactly that masked total.**
**Shard balances DEPLETE WITH TRADING AND DO NOT AUTO-REFILL** — and **no `target_balance_allocation` is set on
either account** (`{"allocations": []}`), so deposits land on shard 0 and nothing moves them.

**Karen's `poly_kalshi_mlb` division died SILENTLY for two days** because of this: MLB markets
(`KXMLBGAME`/`KXMLBTOTAL`/`KXMLBSPREAD`) are all on shard 3; her money was stranded on shard 0; her orders
auto-routed to shard 3, found ~$2.45 there (< a ~$5 order) → `insufficient_balance`, while her ~$515 total looked
perfectly healthy. Two hypotheses were RETIRED with evidence: the **price-units bug was REFUTED** (our wire price
`"0.7000"` matches Kalshi's *current* documented V2 dollar-string contract — the 100×-oversized fear was
dissolved, the ~$514 arithmetic was coincidence), and the **four "quantity 0" trades were a LIQUIDITY MISS**
(placed, filled 0 on dead in-game books), not units and not balance. Full diagnosis: `TRANSITION_SESSIONWRAP_2026-08-30.md`
§8 and memory `kalshi-exchange-sharding-2026-08-30`.

**RESOLVED operator-side (2026-08-30):** Jack moved both balances to shard 3 via **Kalshi's Exchange balance
management UI — there IS a Transfer control on that page**, so the Discord reporter's "I can't move it myself" was
NOT the general case. Verified from the API (16:51Z), not screenshots: **Karen shard3=$491.68 / shard0=$0.006**;
**kalshi_jack shard3=$509.80 / shard0=$0.008**. And legacy is **filling again** — the instant the balance landed on
shard 3 (~16:43Z), SDTrading copies placed+filled (MIA×10, KC/CLE×7, CIN/CHC×8, SD×12, TEX×12, all `count=fill`),
`marks: open=5`. The insufficient_balance wall is gone; remaining `blocked_slippage` are normal thin-book rejects.

---

## 2. ★★ THE OPEN DESIGN QUESTION (for the next agent to SCOPE, not for anyone to answer yet)

**What does money management look like across shards once we trade more than baseball?** Shard 3 is Tennis &
Baseball; our 15-category allowlist includes `fed` (Economics) and others that presumably live on shard 0 or
elsewhere. **One account cannot hold all its collateral on two shards at once.** Frame, do not solve:
- Which of our categories map to which exchange shards?
- Does the platform need to move money automatically, or does `target_balance_allocation` solve it (and does that
  move EXISTING balance, or only govern future drift/deposits)?
- What happens when two sub-divisions on ONE account need funds on DIFFERENT shards simultaneously?

This is the design work that gates trading beyond baseball. It is captured in the backlog ([[prediction-markets-backlog]]).

---

## 3. ★ WHAT THE NEXT AGENT IS FOR (Jack's words)

**Fix the shard money-management question, ARM Jack-MLB, work the backlog, and finish the build.** **Arming is NO
LONGER BLOCKED** — the 100×-oversized fear was refuted (price format is correct) and kalshi_jack is funded on the
right shard ($509.80 on shard 3), so R7.f's first order would fund and place one small, correct, real order.

---

## 4. R7.f GATE — as it now stands

Before the first live order:
1. **★ Verify shard-3 balance AT arm time** (new — orders auto-route to the baseball shard; the total masks the
   split; the shard-3 balance is finite and depletes with trading, so confirm it *when arming*, not from a stale
   read). This REPLACES the old "re-verify price units" item — that is DONE (format is correct).
2. **The six-item go-live gate** (verbatim in `TRANSITION_SESSIONWRAP_2026-08-30.md` §1 / `R7_PLAN_2026-08-29.md`
   §1): dry-run parity → successful-POST proof → kill-switch proven → sign-convention read → **ARM + PLACE ONE
   ORDER (irreversible)** → reconcile the fill → idempotency across restart → disarm.
3. **★ THE FIRST RECONCILE AFTER THE FIRST FILL IS LOAD-BEARING and must be inspected BY HAND** — `position_fp`'s
   sign is STILL UNPROVEN (`boot_reconcile.py` assumes `>0`=long-YES / `<0`=long-NO; the reader only ever used
   `abs()`; R7.a found the account flat, so `+YES/-NO` is neither confirmed nor refuted; it is the 6th NO-leg-lens
   instance). If it inverts, that changes `boot_reconcile.py` before anything else proceeds.

---

## 5. BACKLOG → [[prediction-markets-backlog]] (do not duplicate)

The queued rungs live in the backlog memory file: the three **shard money-management items** (explicit
`exchange_index`; a shard-aware balance read; a `target_balance_allocation` for sustained copying), the Stage-4
follow-ups (widen `_cmd_search` failure capture; the two-wallet PK re-pull; the seven cap-truncated whales; the
doubleheader ticket before R8), and the R7 ladder (R7.f→g→h→i→R8).

---

## 6. FRESH-AGENT ESSENTIALS (unchanged; full detail in `TRANSITION_SESSIONWRAP_2026-08-30.md` §4)

**Standing lessons:** (1) NAVIGATION MUST BE DRIVEN BY EXISTENCE, NOT DATA PRESENCE (the tile defect — a container
outliving its contents). (2) A FEATURE IS NOT SHIPPED UNTIL SOMETHING CAN INVOKE IT (pm_cli search fell between
two rungs). (3) THE GROUNDED SEARCH COST MODEL (separate the median whale from the cap-hitting tail; price calls at
~3 s under throttle → ~1500-2000 calls / ~75-100 min for ~50 Sports whales). (4) NEW — **THE TOTAL BALANCE MASKS
THE PER-SHARD SPLIT** (§1).

**Six box quirks:** (1) pytest needs `-p no:pytest_ethereum`; (2) `az vm run-command` serializes+truncates long
output; (3) box `tar` lands mode 664 → force `chmod 644`+assert; (4) ssh/scp via System32\OpenSSH (Sysnative for
32-bit); (5) `trading_corp/data/` is ROOT-owned (matcher deploys via az-root) but `prediction_markets/` IS
azureuser-writable (ssh); (6) pm_web/engine restart needs az-root (`systemctl restart …` via `az vm run-command`),
file deploy is ssh. Extras: pkg at `/home/azureuser/trading_corp/trading_corp/prediction_markets/`;
`scripts/pm_cli.py` the FILE is azureuser-writable though the DIR is not; service-env creds load via
`KEY_VAULT_URI` (sourceable read-only from the running engine's `/proc/<pid>/environ`).

**Rules:** EXPLICIT MANIFEST (never `git diff prod-live..branch`); Gate-A checks TRANSITIVE imports in the service
dir before any restart; the sanctioned channel = a Jack-executed `.ps1` per the [[command-paste-rule]] (present one
one-liner, wait for board authorization); deploy ≠ prod-live advance (FF-only, message states what it does NOT
contain).

**Settled rulings (do NOT re-litigate):** category existence = the 15-cat allowlist (single edit point); search
backfill on-demand (Ruling 1); candidate write gated `backfill_complete=1`; rank cost-ROI never win%; R7 exclusivity
DONE, driver=engine-task, placement=option-b, `fixed_stake_usd=0.01`→1ct, liquidity floor=`liquidity_ratio*notional`.

**★ The NO-LEG lens (applied six times):** a bare magnitude passes a YES↔NO side-flip. A NO leg's price is 1−P,
its notional count·(1−P), its `position_fp` sign negative. Always ask: would this computation silently accept a
YES↔NO inversion? The 6th instance — `position_fp`'s sign — is UNPROVEN and hand-inspected at the first fill (§4).

---

## 7. HOUSEKEEPING (list + recommend; nothing deleted — Jack authorizes)

**On the box (`~`) — this session's, KEEP as rollback until Jack is done validating; each restore would REGRESS a
verified-live deploy:** `pm_tilefix_deploy_backup_20260830T062306Z` (restore = re-introduce the tile bug),
`pm_cli_search_deploy_backup_20260830T024732Z` + `pm_cli_search_deploy_marker` (restore = un-ship pm_cli search),
`s4deploy_backup_20260830T011111Z` (restore = un-ship Stage 4), `pm_search_run_20260830T032934Z.log` (the run's
estimate-miss + PK evidence — KEEP as the classification input for backlog #5). **This session's read-only sharding
runners created NO backups.**

**Prior sessions (NOT this session; REMOVE candidates, Jack authorizes) — inert intermediates, restoring nothing
useful now the deploys are live:** `r7e_/r7f_backup_*` (R7 rollbacks — KEEP while R7 is mid-ladder), `mace_*`
backups, the 2026-08-27 `pm_stage0/1/t1/rollup2` `*_dbbackup_*.db{,-wal,-shm}` (old PM-DB snapshots),
`pm_cp3a_gate2_pmcli_backup*.py.bak`, `pead_paper_purge_backup_2026-06-25.sql`, `~/backups`, and the leftover deploy
tars (`mace_*.tar`, `pm_cp3a_gate2.tar`, `pm_r7e_*.tar`, `pm_r7f_deploy.tar`, `pm_rung2/stage2/t1_deploy.tar`) + the
`/tmp/pm_*` scratch (clears on reboot). None are mine to delete.

**Local (`C:\Users\AA Incorporado\`):** KEEP the active worktree `cc-pm-stage3-r55-2026-08-29-wt` (@ this doc's
commit) and `cc\*.ps1/.sh` runners (audit trail + re-runnable, e.g. the shard probes + `pm_finalwrap_verify.*`).
The leftover local branch `prodlive-advance-2026-08-30` (→ `e5fbc60`, already on origin/prod-live) is a spent
pointer — safe to delete (`git branch -D`). The ~90 other worktrees are prior sessions / other divisions
(bitunix/mace/pead/older PM) — cleanup candidates, not this session's, not mine to remove.

---

*End of transition. A fresh agent takes it from here: scope the shard money-management design, then arm Jack-MLB
per §4 and finish the build. R7 remains untouched by this session.*
