# PREDICTION MARKETS — TRANSITION TO NEXT AGENT (2026-08-31, FINAL / SESSION WRAP 3)

> **★ THIS SUPERSEDES `TRANSITION_SESSIONWRAP2_2026-08-30.md` — read THIS first.** That doc ended with
> *"nothing is armed; no order has ever been placed."* **THAT IS NO LONGER TRUE.** Read the banner below before
> you touch anything.

---

## ‼️ DO NOT MISREAD THIS STATE — JACK-MLB IS ARMED AND TRADING LIVE

**The `kalshi_jack / mlb` sub-division is ARMED, effective_armed=TRUE, and trading with REAL MONEY, UNATTENDED,
right now.** It will place up to **20 orders/day at 5 contracts each** across **two whales — SDTrading and
xifutloong3** — on **three market types (moneyline `KXMLBGAME`, totals `KXMLBTOTAL`, spread `KXMLBSPREAD`)**.
Arm timestamp **2026-08-31T02:35:39.135299Z**. This is deliberate — Jack authorized R8 arming as the threshold
step. **Do not "clean up" by disarming. Do not assume idle.**

**The monitor is STOPPED.** I stopped my polling watch at end of session (Jack's instruction: the system stays
armed and trading, but the agent goes offline). **Therefore the first R8 placement will be discovered by READING
the journal, not reported live.** A fresh agent picking this up should first read `pm_subdivision_order` for any
`dry_run=0` row with `response_ts > 1788136539` (the arm epoch) — that is the first real R8 trade.

### ★ THE STOP COMMAND — verbatim, keep reachable (from repo root on the box)
```
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global
```
Persisted the instant it returns; the driver re-reads the kill before every order, so the next order is blocked
within ~7 s (`poll_sec`) worst case. One already-POSTED order is not recalled (the irreducible one-order window).
Full card: `STOP_PROCEDURE.md`. To resume after a latch: `RESUME_PROCEDURE.md` (**raise the cap BEFORE clearing
the latch**).

---

## 1. ★★ THE FIRST TRADE HAS SETTLED — IT LOST. AND IT LEFT A LIVE JOURNAL-vs-VENUE DIVERGENCE.

This is the most important operational fact for whoever reads this next. Established read-only at
**2026-08-31T03:00Z**, nothing changed.

### What happened
The platform's first-ever live order (id=1, placed 2026-08-30 ~22:14Z) bought **1 contract YES on
`KXMLBGAME-26AUG301920CINCHC-CHC` (Chicago Cubs to win) @ 0.60 + $0.0084 fee**. The Cubs game (19:20 ET start)
finished; it was marked ~$0.08 for hours (losing) and rode to resolution.

- **Market resolution (venue):** `status=finalized result=no` → **Cubs LOST → our YES settled to $0.00.**
  **First trade is a realized loss of ~−$0.6084** (0.60 cost + 0.0084 fee, no proceeds).
- **Position now:** **GONE** from `/portfolio/positions` (settled/closed on the venue).
- **Settlement record (venue):**
  `{event_ticker: KXMLBGAME-26AUG301920CINCHC, exchange_index: 3, market_result: no, yes_count_fp: 1.00,
  yes_total_cost_dollars: 0.600000, revenue: 0, value: 0, settled_time: 2026-08-31T02:44:41.420484Z}` —
  **booked on shard 3** (exchange_index=3, consistent with the sharding finding).

### ★ The shard-proceeds question — this trade CANNOT answer it (it needs a WINNER)
Shard-3 timeline: pre-order **509.8040** → post-fill **509.1956** (−0.6084 cost+fee) → **now 509.1956, delta
0.0000**. A **losing** YES settles to **$0 → no proceeds credited → shard-3 is unchanged from post-fill.** So we
have PROVEN the *debit* side lands on shard 3 (the cost came off shard 3, the settlement booked on shard 3), but
we **still cannot answer "do settlement PROCEEDS return to shard 3, or sweep to shard 0?"** — that requires a
**winner's ~$1.00 credit** to observe where it lands. Carry this: **the proceeds-shard direction is unproven; the
first WINNING settlement is load-bearing for it.**

### ★ A JOURNAL-vs-VENUE DIVERGENCE EXISTS RIGHT NOW, undetected — and that is BY DESIGN
- **Our journal** (`pm_subdivision_order` id=1) still reads `outcome_status='filled'` and **holds +1 YES.** There
  is **no settlement-close path** (that is boot_reconcile rung R-d, deferred/not built). The journal does **not**
  know the market settled.
- **`/live/kalshi_jack/mlb`** still shows `KXMLBGAME-26AUG301920CINCHC-CHC` under **"Currently held"** — because
  the page is journal-derived, and the journal still holds it.
- **The venue is FLAT** (position gone, settled). **So journal (+1) ≠ venue (0) as of the 02:44:41Z settlement.**
- **boot_reconcile came up CLEAN on both of today's restarts** — `reconciled=True latched=False` at
  **01:26:58Z** (Ruling-A restart) and **02:14:05Z** (sizing restart). **Both PRECEDED the 02:44:41Z settlement**,
  so the venue genuinely still held +1 at each — the clean reconcile was **correct**, not a miss.
- **★ THE NEXT ENGINE RESTART WILL LATCH `boot_reconcile_mismatch` (R-b, settlement drift) — journal holds +1,
  venue flat — and this is EXPECTED, NOT A FAULT.** boot_reconcile runs only at engine boot; there has been no
  restart since settlement, so the drift is currently undetected. When the engine next restarts, R-b (JOURNAL_ONLY:
  journal holds, venue flat = settlement drift) will fire, the sub will latch, and **a human must confirm the Cubs
  loss and clear the latch** (`RESUME_PROCEDURE.md`). **Do not be alarmed and do not "fix" it — this is the
  reconcile working as designed.** It is the first real-money exercise of the settlement-drift branch.

**Nothing above was changed. It is all reported, not fixed.**

---

## 2. STATE (observed 2026-08-31T03:00Z, read-only)

| Thing | Value |
|---|---|
| Arm | `effective_armed=true`, `latched=false`, global armed + sub armed, ts **2026-08-31T02:35:39.135299Z** |
| Orders | `pm_subdivision_order` total **1**, dry_run=0 **1** (the Cubs loss); **0 placed since the 02:35:39Z arm** |
| Attachments (active) | **2** — SDTrading + xifutloong3 (both whales, live ⊆ pinned) |
| Resolved config (via `sub_config_from_row`) | `sizing_mode=contracts` · **contracts=5** · **max_orders_per_day=20** · **per_order_usd_cap=5.50** · **daily_usd_cap=60** · **max_open_usd=60** · slippage=2¢ · liquidity_ratio=0.75 |
| Schema | **14** (migration 014 = `contracts` column) |
| Engine `trading-corp` | MainPID **107937**, NRestarts 0, active |
| pm_web `prediction-markets-web` | MainPID **108138**, active (loopback 127.0.0.1:8081 behind Authelia) |
| Cron | 4 PM crontab entries (paper-poll `*/30`, refresh `0 5`, paper-adjudicate `40 5`, paper-rollup `50 5`) + service timers; all unchanged |
| HTTP surfaces | `/healthz /  /farm /farm/mlb /live /live/kalshi_jack/mlb` all **200** |
| Branch | `pm-shard-scope-2026-08-30` @ **78eb5e4** before this doc (local==origin) |
| prod-live | local `7220e32` / origin `e5fbc60` — **NOT advanced this session** |

---

## 3. LIVE CONFIG — and how to change it (no restart needed)

All caps and the sizing mode are **columns on `pm_subdivision`** (row `account_id='kalshi_jack' AND
category='mlb'`), and `execution.sub_config_from_row` **reads them PER CYCLE**. So:

- **To change a cap or the contract count: write the column, done — the driver picks it up within ~7 s, NO
  restart.** There is deliberately **no CLI for caps** (a config write, back the DB up first). Pattern in
  `RESUME_PROCEDURE.md` §1. Always **verify via `sub_config_from_row`, not a raw column read-back** (resolved
  value is what the driver actually sees).
- **`sizing_mode='contracts'`** means the order size is a **flat `contracts` count** (currently 5), independent of
  the whale's dollar size. `contracts` is changeable live like any cap. `'fixed'` (legacy flat-dollars) and
  `'kelly'` (carried, not built) also exist; an unrecognized mode logs a LOUD warning and falls back to fixed;
  `contracts < 1` logs a LOUD warning and clamps to 1.
- **To STOP:** `STOP_PROCEDURE.md` → `live-disarm --global`. **To RESUME after a latch:** `RESUME_PROCEDURE.md` →
  **raise the cap BEFORE clearing the latch** (clearing while `max_orders_per_day` is exhausted for the UTC day
  re-latches on the next order).

---

## 4. ★ STILL UNPROVEN — the load-bearing gaps to carry

1. **The −NO leg has NEVER filled.** Both the first fill and the R7.g reconcile proof were a **YES** buy — so the
   **+YES** half of the `position_fp` sign is PROVEN on real data, but the **−NO** half is still **inference**.
   boot_reconcile's NO branch (`journal_signed` for a short/NO position) is likewise unexercised. **★ THE FIRST NO
   FILL IS LOAD-BEARING: when it happens, STOP and hand-inspect R7.g-style** (journal signed-net per ticker vs
   venue) before trusting the reconcile. This is standing lens #1 (NO-leg inversion) with real money at stake.
2. **Settlement proceeds-shard direction unproven** (see §1) — needs the first WINNING settlement to see whether
   the ~$1.00 credit returns to shard 3 or sweeps to shard 0.
3. **Whale-exit is NOT built.** The platform can **OPEN** a copy but has **no path to CLOSE** from a whale's exit —
   every position rides to settlement. (This is the Cubs D1 argument, §5.)
4. **Finding 5 — the engine refuses same-market re-entry.** Idempotency dedups by `coid =
   uuid5(division|wallet|ticker|leg|signal_id)`, so the same whale adding to a market, or a second whale on the
   same (ticker,leg), will **not** double-place on that key. Known constraint; the tx_hash dedup key that would
   relax it rides with Option D (it reads `/activity`).

---

## 5. ★ THE CUBS POSITION — the concrete D1 argument for whale-exit (Option D)

We bought Cubs YES @0.60. It was marked **~$0.08 for hours** — clearly losing — and, with no exit path, **rode all
the way to $0**. A live exit signal (the whale sold, or a stop) could have salvaged ~$0.08 rather than surrendering
the whole stake. **This is the Day-1, real-money argument that "open-only, ride-to-settlement" leaves money on the
table, and that Option D (whale-exit / early close) is the highest-value next build.** *Carry, do not act:* this is
an R8 observation to inform the Option-D design, not a green light to build tonight.

---

## 6. R8 SUCCESS / FAILURE CRITERIA — NOT like-for-like with legacy, by design

R8 is the **platform's own money path** (`kalshi_jack`), a **different division** from legacy `poly_kalshi_mlb`
(Karen): different account, different caps, **flat-5-contracts** sizing (not legacy's), and its own shard funding.
**Do not judge R8 by legacy's fill rate or PnL.**

- **Success looks like:** copies open on the **correct ticker/leg/count**; caps **bind on the right failures**
  (count ceiling on order #21, `per_order`/`daily`/`open` on dollars, slippage on thin books, shard-underfunded if
  shard 3 depletes); boot_reconcile stays **honest** (clean when venue matches, latches on real drift — e.g. the
  settlement drift in §1); no NO-leg sign inversion; no runaway.
- **Failure looks like:** a **NO-leg sign inversion**, a **cap that fails open** (standing lens #2), an order placed
  **after disarm**, a **phantom fill**, or a reconcile that is **silently wrong** (clean when it should latch, or
  latched on a false drift).

---

## 7. ★ NEXT SESSION — in Jack's priority order (all UNAUTHORIZED until Jack says go)

1. **OPTION D — whale-exit / early-close order path.** The top build. Full adversarial review (it moves money out).
   The **Finding-5 `tx_hash` dedup key rides with it**, since Option D reads the whale's `/activity` to detect an
   exit.
2. **STAGE 5 — Analyze loss-completeness.** Independent and **unblocked** (can run in parallel with anything); does
   not touch the money path.
3. **WHALE-PROPORTIONAL INVESTIGATION.** Question first, build only if the data says so: *does a whale's
   larger-than-typical bet win more / return more per dollar?* If not, **don't build it.** Scope must include:
   per-whale distribution **shape**, **normalisation** across whales, a **new-whale fallback threshold** (what to do
   before a whale has history), and the **loss-omission contamination bound** (whales whose losses we never saw).
4. **ACCOUNT PAGES + GLOBAL ARM UI.** With the hard constraint that **the kill path NEVER depends on pm_web** (the
   CLI disarm must always work with pm_web down).
5. **BACKLOG:** `/orderbook` depth precision · **doubleheader ambiguity resolved BEFORE R8 widens** (ticket
   `TICKET_doubleheader_matcher_ambiguity_2026-08-30.md`) · cron alerting · a `flock` guard on the poll jobs.

---

## 8. STANDING REVIEW LENSES — apply to every rung (Jack ruled these first-class)

1. **NO-leg inversion** — a sign/leg flip that inverts a position. The −NO half is still unproven (§4.1).
2. **A safety check that silently STOPS checking (fails OPEN).** For EVERY gate ask: *what input value makes it pass
   everything, and is that value reachable on live?* (The `_market_quote_dict` yes_bid omission was one; the
   liquidity_ratio=0 clamp was another.)
3. **A green test suite that never runs the real path.** Every flag-gated rung needs **≥1 test with the flag ON vs
   stubs** that actually exercises placement — a DISARMED suite proves nothing about the armed path.
4. **A fixture must mirror the real object.** A test double that diverges from the live row/response (a column it
   lacks, a field shape it fakes) proves nothing.
5. **Gate-never-passes → suspect the INPUT before the logic** (the yes_bid bug rejected every book because the quote
   dict omitted a field — the gate was fine).
6. **A deploy set with mixed schema tolerance → the MIGRATION leads.** Ordering is load-bearing (two
   schema-tolerance levels; the sizing S1–S4 deploy proved this — the `contracts` migration had to land before the
   code that SELECTs it).

---

## 9. BOX QUIRKS + OPERATING RULES — do not relearn these the hard way

**Six box quirks:**
1. Package lives at **`/home/azureuser/trading_corp/trading_corp/`** (nested — the repo root is the outer dir).
2. PM + config files are **azureuser-writable via ssh**; the **ONLY** thing that needs **az-root** is the
   **restart**.
3. **An engine restart bounces bitunix** (24/7 futures) — give a heads-up before restarting `trading-corp`.
4. **pm_web is loopback** `127.0.0.1:8081` behind **Authelia** — reach it on the box, not from outside.
5. `main.py` has **`.gitattributes eol=lf`**; a Windows worktree checks it out **CRLF**, so a wholesale diff looks
   like the whole file changed. **Edit surgically; verify on the box with `diff --strip-trailing-cr`.**
6. `_row_get` **tolerates missing `sqlite3.Row` columns** (IndexError→None) — a pre-migration Row won't crash, it
   reads the default.

**Operating rules (carry verbatim):**
- **command-paste-rule (HARD):** all box commands go through **Jack-authorized `.ps1` runners in `cc\`** with a
  **PURE-ASCII `.sh`** payload, **STDIN-piped**: `Get-Content -Raw $sh | ssh $h "tr -d '\r\357\273\277' | bash"`;
  validate the `.sh` parses and has **0 bytes >127** before running. **az-root restarts use Jack's canonical
  scripts** (`Desktop\restart_tc.ps1` engine, `restart_pmweb.ps1` pm_web), never ad-hoc.
- **explicit-manifest:** a deploy manifest must list **every** file. (The `boot_reconcile.py` miss — an import the
  edited file needed but the manifest omitted — cost a failed restart.)
- **transitive-import Gate-A:** a pre-deploy gate must check **transitive** imports, not just the edited file.
- **hash-as-gate-not-grep:** gate a deploy on a **content-hash match**, not a grep marker (markers span line breaks
  and produce false negatives — this bit the Ruling-A deploy; the hash was the real gate).
- **VC / main.py landmine (closed this session):** the branch `main.py` now contains the R7.e PM-driver wiring
  block and is **content-identical to the box**. A future `main.py` deploy from the branch is safe — but **a restart
  from any OLDER `main.py` checkout would DELETE the running PM driver.** Confirm the wiring block is present before
  any `main.py` deploy/restart.

**Settled rulings — DO NOT re-litigate:** exclusivity done · driver = engine-task · placement = option-b ·
xifutloong3 re-attached (R6) · sizing is a real `'contracts'` mode, not the `fixed_stake=0.01` hack ·
`daily_usd_cap`/`max_open_usd` = **$60** (not $100 — dollar caps must bind on a *different* failure than the count
cap) · resolved-verify via `sub_config_from_row` · a `main.py` deploy would delete the running driver unless the
wiring is present.

---

## 10. HOUSEKEEPING — backups: DANGEROUS vs merely obsolete (nothing deleted; recommendations only)

**★ DANGEROUS (do NOT restore these onto the live money DB — a restore would silently REVERT `sizing_mode` and the
caps on a LIVE, ARMED, TRADING division).** These are pre-caps / pre-migration snapshots of
`data/prediction_markets.db`:
- `~/pm_caps_write_backup_20260831T022238Z.db` (pre the R8 caps write — restoring reverts contracts/20/5.50/60/60)
- `~/pm_reattach_backup_20260831T022934Z.db` (pre xifutloong3 re-attach)
- `~/pm_mig014_backup_20260831T020430Z.db` (**pre-schema-14** — restoring drops the `contracts` column)
- `~/pm_caps_set_backup_20260830T180415Z.db` (older pre-caps snapshot)

**Merely OBSOLETE (safe to remove whenever — they are code/scratch, not the live DB):**
- `~/pm_rulingA_backup*` and `~/pm_sizing_code_backup*` directories (superseded code backups)
- any leftover old `~/*.tar` / `/tmp/pm_*` scratch. (This session's own tars self-cleaned.)

**Recommendation:** keep the four DANGEROUS DB backups (they are the rollback path) but **label them clearly and
never `cp` one back without disarming first**; delete the obsolete code-backup dirs at leisure. I deleted **nothing**
— this was a read-only wrap.

---

## 11. WHAT THIS SESSION DID (for the record)

First fill captured + **+YES** sign snapshot banked · `/live` invisible-filled-order defect fixed & deployed
(pm_web only) · **R7.g** reconcile MATCH (+YES proven; −NO still inference) · **R7.h** idempotency-across-restart
proven · **R7.i** disarm proven + **Ruling A** fixed (double-fault must not fall through into the trading loop) +
**Ruling B** recorded (Kalshi `client_order_id` backstop is an *assumption*, not verified) · **main.py VC gap
closed** (branch == box) · **flat-contracts sizing** built, reviewed, deployed (S1–S4 green, migration-first) · **R8
caps written** (resolved-verify PASS) · **xifutloong3 re-attached** · **ARMED for R8** · first trade **settled a
loss** (§1). Ledgers: `SIZING_DEPLOY_2026-08-31.md`, `R8_PLAN_2026-08-31.md`, `LIVE_PAGE_DEPLOY_2026-08-30.md`,
`RESUME_PROCEDURE.md`, `STOP_PROCEDURE.md`.

---

*Written 2026-08-31 at session wrap. The division is ARMED and TRADING; the monitor is STOPPED; the next placement
will be found by reading `pm_subdivision_order`, not reported live. STOP =
`PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`.*
