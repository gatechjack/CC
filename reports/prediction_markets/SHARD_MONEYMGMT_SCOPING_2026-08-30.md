# Shard Money-Management — SCOPING (Task 1, read-only; Jack rules, I do not pick)

**Date:** 2026-08-30 · **Branch:** `pm-shard-scope-2026-08-30` (off PM tip `63ce899`) · **Status:** scoping only,
nothing built. Design reasoning below is COMPLETE and doc-cited; two boxes are EMPIRICAL and are filled by the
read-only probe `pm_shard_scope_probe.ps1` (presented for authorization, not yet run): the per-category
exchange_index map (§2) and the current `target_balance_allocation` state (§4). This doc will be finalized with
that output.

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

| category | expected shard (docs default) | series probed | empirical exchange_index |
|---|---|---|---|
| mlb | **3** (Baseball) | KXMLBGAME | _pending probe_ |
| atp | **3** (Tennis) | KXATP* | _pending_ |
| wta | **3** (Tennis) | KXWTA* | _pending_ |
| tennis | **3** (Tennis) | KXATP/KXWTA/KXITF | _pending_ |
| nba | 0 (Default) | KXNBAGAME | _pending_ |
| nfl | 0 | KXNFLGAME | _pending_ |
| nhl | 0 | KXNHLGAME | _pending_ |
| wnba | 0 | KXWNBA* | _pending_ |
| epl | 0 | KXEPL/KXPREMIERLEAGUE | _pending_ |
| ucl | 0 | KXUCL* | _pending_ |
| soccer | 0 | KXMLS/KXLALIGA/KXUEL | _pending_ |
| cs2 | 0 | KXCS2* | _pending_ |
| golf | 0 | KXPGA/KXGOLF | _pending_ |
| ufc | 0 | KXUFC* | _pending_ |
| fed | 0 (Economics) | KXFED* | _pending_ |

**Preliminary structure (docs):** our allowlist splits across **two shards — 3 (mlb/atp/wta/tennis) and 0
(everything else)**; crypto (shard 2) is not in our allowlist. So the money-management problem is fundamentally a
**shard-0 ↔ shard-3 split on any account that runs both baseball/tennis AND another category.** (Probe confirms
the exact split and flags any category the docs' default gets wrong.)

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
"percent":P}]}` (added 2026-08-20). Current state on both accounts: `{"allocations": []}` (none set) — _probe
re-confirms live._

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

**My recommendation (Jack rules):** **B as the mover + items #1 (explicit exchange_index) and #2 (shard-aware
balance read) as prerequisites; A as the fallback until B is verified with small amounts; defer C.** Rationale: the
shard-aware read is the load-bearing safety piece for ALL options (you must be able to SEE the per-shard split
before you trust any auto-mover or operator action); B lets Kalshi — not our unproven code — custody the transfers;
C's risk (our code moving real funds) is only justified by a contention we have not yet observed. **A structural
alternative worth naming:** assign categories to ACCOUNTS so each account is effectively single-shard
(baseball/tennis on one account, shard-0 sports on another) — this sidesteps intra-account sharding entirely at the
cost of more accounts/keys and cross-account capital planning. That is a Jack-level allocation decision, not a code
change.

---

## 7. WHAT THE READ-ONLY PROBE CONFIRMS (before this doc is finalized)

`pm_shard_scope_probe.ps1` (presented; awaiting board authorization) — read-only, no orders/transfers/writes:
- **Part A box state** (schema, all counts incl candidates, arm DISARMED, `pm_subdivision_order=0`, sub-division +
  attachments, PIDs, cron, /healthz//farm//farm/atp//live) — for the Part A report.
- **Per-shard `balance_breakdown`** for kalshi_jack + kalshi_karen (the numbers that matter for the R7.f arm-time
  gate) + current `target_balance_allocation` on both.
- **§2 category→exchange_index** for all 15 categories via `/series/{ticker}` (empirical; MISS-labeled where a
  candidate ticker is wrong, to refine in a tiny follow-up).

*Backlog cross-ref: shard items #1 (explicit exchange_index), #2 (shard-aware balance read), #3
(target_balance_allocation) in [[prediction-markets-backlog]]. This doc scopes #3's design question.*
