# Shard Money-Management — SCOPING (Task 1, read-only; Jack rules, I do not pick)

**Date:** 2026-08-30 · **Branch:** `pm-shard-scope-2026-08-30` (off PM tip `63ce899`) · **Status:** scoping only,
nothing built. Design reasoning is doc-cited; the two empirical boxes are now FILLED from the read-only probe
`pm_shard_scope_probe.ps1`, run under board authorization at **2026-08-30T17:18:42Z** (per-category exchange_index
map §2; current `target_balance_allocation` state §4). FINAL.

---

## RULING (2026-08-30, Jack) — (e) DECIDED

**OPTION B — Kalshi-native `target_balance_allocation` as the mover**, with **A as the fallback until B is verified
with small amounts**. **C (platform-built transfers) DEFERRED** — our code moving real funds is a second money path
needing its own arm/kill/reconcile, and no observed problem demands it. Backlog **#2 (shard-aware balance read) and
#1 (explicit exchange_index)** stand. **★ The shard-aware balance read is LOAD-BEARING under all three options and
is built FIRST, not last** — the masked total with an empty shard is exactly the state that killed Karen's division
for two days. Of the three failure shapes (§3): only the STRUCTURAL split needs solving now; transient contention
is a 10s lag we live with; pure capital contention is not a sharding problem. **Two caveats to carry (both hold):**
"sweepable balance" is undefined in the docs, and a target percentage is ALSO A CAP — neither blocks B, both mean
**verify B with small amounts, do not trust from documentation.** The probe changed nothing in this ruling; it
confirmed the clean shard-0↔shard-3 split and that both accounts have no allocation set.

---

## 0. TL;DR RECOMMENDATION (Jack rules)

1. **For R7.f / Jack-MLB right now: NOTHING here blocks arming.** One category (mlb) → one shard (3),
   kalshi_jack is funded there. Keep the existing gate: **verify shard-3 balance AT arm time**, from
   `balance_breakdown`, not the masked total.
2. **When we trade more than one shard's worth of categories on ONE account, do NOT build platform money-movement
   first.** Kalshi already ships the mover: `target_balance_allocation` runs a **10-second loop that transfers
   EXISTING balance** between shards to hold a declared split (docs quote in §4). Use it.
3. **Two cheap defensive-hygiene items first (backlog #1, #2), both low-risk:** a **shard-aware balance read**
   (read `balance_breakdown`, stop trusting the masked total — kalshi_live.py:278) and an **explicit
   `exchange_index` on the order body** (deterministic routing). Neither moves money.
4. **Building our OWN money-movement (platform-managed transfers) is a serious new capability — defer it** unless a
   real multi-shard-per-account contention is observed that Kalshi's native rebalancer can't cover. Named risk in §6.

The genuine hard limit is **total capital**, not shards: `target_balance_allocation` lets one account HOLD a split
and refills continuously, so "two subs need different shards at once" is only a problem when their simultaneous
need exceeds total capital, or when one sub transiently needs more than its shard's target %.

---

## 1. THE MECHANISM (facts, doc-cited)

Kalshi shards collateral by `exchange_index`. From live docs
(https://docs.kalshi.com/getting_started/exchange_sharding, verified 2026-08-30):

- **Shards:** `0` Default · `1` Combos/Exotics · `2` Crypto (events from 2026-08-24+) · `3` Tennis & Baseball
  tags (events from 2026-08-24+). Enumerable via `GET /exchange/status` → `exchange_index_statuses[]`.
- **★ The shard-3 assignment applies to events created AFTER 2026-08-24.** Older MLB/tennis events may still sit
  on shard 0. **Never assume by sport — read `exchange_index` per market/series.**
- **Orders auto-route** to the market's shard and charge THAT shard's balance, not the total
  (https://docs.kalshi.com/api-reference/orders/create-order-v2). Our order body omits `exchange_index`
  (kalshi_live.py:157-180), so it relies on auto-route.
- **`GET /portfolio/balance` returns `balance_breakdown[]`** (per-shard `IndexedBalance{exchange_index, balance}`)
  BY DEFAULT — omitted only for a subaccount-restricted key. Passing `?exchange_index=N` scopes the top-level
  `balance`/`portfolio_value` to that shard (https://docs.kalshi.com/api-reference/portfolio/get-balance).
  **Our reader `bal.balance / 100` (kalshi_live.py:278) ignores the breakdown and returns the masked total** —
  used only as a `>0` preflight, never per-shard. This is the exact blind spot that killed Karen's division.
- **The gate-6 exposure cap (execution.py:337) reads PM's OWN journal** (`pm_subdivision_order`), never the venue
  balance — so today the platform has NO shard awareness at all in the order path.

---

## 2. (a) WHICH OF OUR 15 CATEGORIES MAP TO WHICH SHARD

**Method:** read `exchange_index` from `GET /series/{ticker}` per category — empirical, not inferred from shard
names (task requirement). The probe (§8) does this. **The PM package wires only MLB series**
(`live_driver.py:57` = `KXMLBGAME/KXMLBTOTAL/KXMLBSPREAD`); the other 14 categories have NO Kalshi series mapping
in PM (legacy arb/copy agents carry ticker prefixes, out of scope to edit). So this map is NEW knowledge.

**EMPIRICAL RESULT (probe 17:18:42Z; `exchange_index` read from `GET /series/{ticker}`; all 15 resolved, 0 MISS):**

| category | series read | **exchange_index** | series title |
|---|---|---|---|
| mlb | KXMLBGAME | **3** | Professional Baseball Game |
| atp | KXATPMATCH | **3** | ATP Tennis Match |
| wta | KXWTAMATCH | **3** | WTA Tennis Match |
| tennis | KXATP | **3** | Men's Tournament Winner |
| nba | KXNBAGAME | **0** | Pro Basketball Game |
| nfl | KXNFLGAME | **0** | Professional Football Game |
| nhl | KXNHLGAME | **0** | NHL Game |
| wnba | KXWNBAGAME | **0** | Women's Pro Basketball Game |
| epl | KXEPLGAME | **0** | English Premier League Game |
| ucl | KXUCLGAME | **0** | UEFA Champions League Game |
| soccer | KXMLSGAME | **0** | Major League Soccer Game |
| cs2 | KXCS2 | **0** | CS2 Tournament Winner |
| golf | KXPGA | **0** | PGA Championship |
| ufc | KXUFCFIGHT | **0** | UFC Fight |
| fed | KXFED | **0** | Fed funds rate |

**CONFIRMED structure — a clean TWO-shard split at the SERIES level:** **4 series on shard 3** (mlb, atp, wta,
tennis) and **11 on shard 0** (nba, nfl, nhl, wnba, epl, ucl, soccer, cs2, golf, ufc, fed). NONE touch shard 1
(Combos) / shard 2 (Crypto). So it is a **shard-0 ↔ shard-3 problem**.

**★★ CORRECTION (Jack read the source, 2026-08-30) — CATEGORY→SHARD IS A PER-MARKET FACT, NOT PER-CATEGORY.**
`docs.kalshi.com/getting_started/exchange_sharding`: *"There is currently no plan to migrate any live market to a
new exchange instance."* Only events created **after 2026-08-24 12:00 ET** land on shard 3; **older live MLB
markets stay on shard 0.** So a series ticker tells you where its NEW events go — but **an individual market's shard
is authoritative only from that market's own `exchange_index`, read at order time.** MLB is therefore **split
across shard 0 AND shard 3 by creation date**, and funding MLB during the transition may need money on BOTH shards,
not just shard 3. This very likely explains Karen: her shard-0 balance stayed usable for OLD markets while shard 3
starved for NEW ones. **Consequence for rung 2: the pre-flight gate MUST resolve the shard of THAT MARKET (the
market object's `exchange_index`), never of the category.** The series map above is a planning aid, not the gate's
input.

---

## 3. (b) THE CRUX — one account cannot hold all its collateral on two shards at once

**Framed precisely:** a single Kalshi account's cash is physically partitioned per shard. If sub-division A (mlb,
shard 3) and sub-division B (nba, shard 0) are both on **the same account**, then at any instant that account's
cash is split between shard 3 and shard 0. Neither sub can spend the OTHER shard's balance. So:

- If you put 100% on shard 3, B (nba) auto-routes to shard 0, finds ~$0, and rejects `insufficient_balance` — the
  masked total looks healthy (Karen's exact failure).
- If you split 50/50, each sub is capped at ~50% of the account's capital on its shard at any moment.

**This is a real design problem, and it has three shapes:**
1. **Structural** (which sub uses which shard) — solved by declaring a split (§4) or by one-shard-per-account (§6).
2. **Transient contention** — a sub momentarily needs > its shard's declared %; the 10s rebalancer lags.
3. **Capital contention** — the SUM of both subs' simultaneous needs exceeds the account's total. No sharding
   scheme fixes this; it's a funding decision.

---

## 4. (c) DOES `target_balance_allocation` SOLVE IT?

**Endpoints:** `GET`/`POST /portfolio/target_balance_allocation`; body `{"allocations":[{"exchange_index":N,
"percent":P}]}` (added 2026-08-20). **Current state (probe 17:18Z): both accounts `{"allocations": []}` — none
set.** And `GET /portfolio/balance` DOES return `balance_breakdown` per shard by default (probe confirms:
kalshi_jack shard3=$509.80/shard0=$0.008; kalshi_karen shard3=$491.68/shard0=$0.006) — our reader just ignores it.

**★ What it does (direct doc quote, https://docs.kalshi.com/getting_started/exchange_sharding):**
> "Users may opt in to automatic rebalancing between exchange shards by supplying a target balance allocation as a
> percentage of their balance across exchange shards. **Every 10 seconds, Kalshi computes the customer's balance on
> each exchange shard as its account balance minus the value of its resting orders. If the balance has drifted from
> the target allocation, Kalshi executes an intra-exchange account transfer on the customer's behalf** to restore
> the target allocation."

**So it MOVES EXISTING balance** — not future-deposits-only. This **contradicts the Discord reporter's "it didn't
move existing funds"**; the live docs describe a continuous mover on the current balance. It DOES hold a split
(e.g. `[{0:50},{3:50}]`), and it DOES auto-refill a traded-down shard from another, on a 10s cadence.

**★ What it does NOT resolve (the ambiguities — the crux of §6, undocumented):**
1. **10s lag vs fast drain.** If a shard is drained (or a big order needs more than the shard holds) between
   rebalance cycles, does the order reject `insufficient_balance` before the next transfer? **Docs do not say.**
   A 1-contract ~$0.50 order won't hit this; sustained/large copying might.
2. **"Sweepable balance" is undefined** in the docs — unclear if the % is over total cash or cash net of locked
   collateral. Matters for how much actually sits on each shard.
3. **A target % is a CAP as well as a floor.** Declaring shard-3 = 50% means that account never holds >50% on
   shard 3 — so a baseball-heavy day can be starved even though total is fine. The split must match expected load,
   and load shifts by day/sport-season.
4. **Resting orders reduce the computed shard balance** → possible thrash between "reserve for a resting order"
   and "rebalance away the free cash."

**Verdict (mine, for Jack to rule):** `target_balance_allocation` is the RIGHT primitive and **is Kalshi moving the
money, not our code** — much lower risk than building our own transfers. It solves the structural split and the
slow-drain refill. It does NOT guarantee against a fast-drain reject or a bad split ratio; those need a
shard-aware balance read + sane per-shard sizing, not a money-mover.

---

## 5. (d) DOES AN EXPLICIT `exchange_index` ON THE ORDER CHANGE ANYTHING?

**No — routing destination is unchanged.** Order body field `exchange_index` is optional
(https://docs.kalshi.com/api-reference/orders/create-order-v2): "If omitted, auto-routes when ticker is provided;
otherwise defaults to 0. Use -1 to require auto-routing by ticker." Setting it to the market's shard (or `-1`)
makes routing **deterministic/explicit**, and would surface a mismatch loudly instead of silently routing
somewhere unexpected — but it does not put money on the shard. **The problem is where the money SITS, not where the
order routes.** So: worth doing as cheap defensive hygiene (backlog #1), NOT a fix for §3.

---

## 6. (e) SHOULD THE PLATFORM MOVE MONEY AUTOMATICALLY? — options + failure modes

Automatic movement of real funds is a serious capability; I am NOT picking. Three options:

**Option A — Operator-only (status quo).** Jack moves funds via Kalshi's Exchange balance-management UI (as he did
today for Karen). *Failure modes:* silent depletion is invisible until an order rejects (the 2-day Karen outage);
does not scale past one or two categories; REQUIRES a per-shard balance alarm to be safe (which we don't have —
item 2). *Cost:* none to build; ongoing human attention.

**Option B — Kalshi-native `target_balance_allocation` (RECOMMENDED as the mover).** Set a per-account split once;
Kalshi's 10s loop moves existing balance to hold it. *Failure modes:* the four ambiguities in §4 (10s lag vs fast
drain; undefined "sweepable"; the % is a cap; resting-order thrash). *Cost:* one authenticated POST per account +
a shard-aware read to choose/verify the split; **no custody risk from our code** — Kalshi custodies the transfer.

**Option C — Platform-managed transfers (build our own money movement).** PM computes needed per-shard funding
from pending signals and calls Kalshi's intra-exchange transfer before placing. *Failure modes:* this is REAL money
movement **in our code** — a bug moves real funds to the wrong shard/account; it races Kalshi's own rebalancer if
both run; it adds a new authenticated WRITE path (transfer) that must be armed / kill-switched / reconciled exactly
like the order path (a whole second money path to prove out); heavier reconciliation. *Cost:* high build + high
risk; *benefit:* only over B if we hit a real fast-drain or per-shard-cap wall B can't cover.

**RULED (Jack, 2026-08-30):** **B as the mover; #2 (shard-aware balance read) FIRST and load-bearing; #1 (explicit
exchange_index) alongside; A as the fallback until B is verified with small amounts; C DEFERRED.** Rationale: the
shard-aware read is the load-bearing safety piece for ALL options (you must be able to SEE the per-shard split
before you trust any auto-mover or operator action); B lets Kalshi — not our unproven code — custody the transfers;
C's risk (our code moving real funds) is only justified by a contention we have not yet observed. **A structural
alternative worth naming:** assign categories to ACCOUNTS so each account is effectively single-shard
(baseball/tennis on one account, shard-0 sports on another) — this sidesteps intra-account sharding entirely at the
cost of more accounts/keys and cross-account capital planning. That is a Jack-level allocation decision, not a code
change.

---

## 7. PROBE RESULT (read-only, 2026-08-30T17:18:42Z, board-authorized)

`pm_shard_scope_probe.ps1` ran clean — no orders/transfers/writes. Key observations:
- **Balances:** kalshi_jack shard3=**$509.80** (shard0 $0.008, s1/s2 $0); kalshi_karen shard3=**$491.68** (shard0
  $0.006). Both `target_balance_allocation = {"allocations": []}`. R7.f's ~$0.50 MLB order funds + places on shard 3.
- **§2 map:** all 15 categories resolved; 4 on shard 3, 11 on shard 0 (table above).
- **Box state (Part A):** engine 76416 / pm_web 89704 (NRestarts 0), schema 13, arm DISARMED (0 pm_live rows),
  `pm_subdivision_order=0` (no order ever placed), sub-division `kalshi_jack/mlb` active, active attachment =
  SDTrading only (xifutloong3 detached, active=0), 4 cron entries present, all HTTP 200.
- **★ FLAGGED FOR TASK 2 (not a Task-1 concern):** the `kalshi_jack/mlb` sub-division has **NULL caps** —
  `max_open_usd`, `per_order_usd_cap`, `daily_usd_cap`, `max_orders_per_day`, `max_slippage_cents`, `liquidity_ratio`
  are all NULL (only `fixed_stake_usd=0.01` + `market_types` set). `liquidity_ratio` NULL→0.75 by the R7.f floor,
  but gate-6 (`execution.py:337`) does `sub.max_open_usd + 1e-9` — a NULL there must be verified to coerce safely
  (default vs TypeError) BEFORE arming. Pre-arm code check, surfaced not resolved.

*Backlog cross-ref: shard items #1 (explicit exchange_index), #2 (shard-aware balance read), #3
(target_balance_allocation) in [[prediction-markets-backlog]]. This doc scopes #3's design question.*
